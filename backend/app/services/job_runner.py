"""
Executes the Jobs created by the WorkflowExecutor (Phases 5-9).

    DIGEST  -> atomic master election / merge, delayed until the window closes
    EMAIL / IN_APP -> preference check -> stable Message -> provider -> retry

Each unit of work claims its Job with an atomic UPDATE, so concurrent workers
cannot run the same step twice. Delayed work (digest windows, retry backoff) is
persisted in PostgreSQL (``delayUntil``) and picked up by ``process_due``.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DigestWindow, Job, Message, Notification, Subscriber, Workflow
from app.db.session import SyncSessionLocal
from app.providers.ai import DigestSynthesizer
from app.providers.email import EmailProvider, EmailSendError, get_email_provider
from app.services import content, digest_service
from app.services.preferences import STEP_TO_CHANNEL, is_channel_enabled

logger = logging.getLogger("herald.runner")

_ACTIVE = ("PENDING", "DELAYED", "RUNNING")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dedup_key(subscriber_id: str, workflow_id: str, step_type: str, transaction_id: str) -> str:
    """Stable identity of one logical delivery; shared by every retry attempt."""
    return hashlib.sha256(f"{subscriber_id}|{workflow_id}|{step_type}|{transaction_id}".encode()).hexdigest()


def backoff_seconds(attempt: int) -> float:
    return settings.RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))


class JobRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SyncSessionLocal,
        email_provider: Optional[EmailProvider] = None,
        synthesizer: Optional[DigestSynthesizer] = None,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self._sf = session_factory
        self.email = email_provider or get_email_provider()
        self.synth = synthesizer or DigestSynthesizer()
        self._clock = clock

    # ── entry points ──────────────────────────────────────────────────────────
    def run_notification(self, notification_id: str) -> None:
        """Advance one notification: digest gate first, then delivery steps."""
        with self._sf() as s:
            n = s.get(Notification, notification_id)
            if n is None:
                return
            wf = s.get(Workflow, n.workflow_id)
            jobs = s.execute(
                select(Job).where(Job.notification_id == n.id).order_by(Job.created_at, Job.id)
            ).scalars().all()
            pending_delivery: List[str] = []
            for job in jobs:
                if job.step_type == "DIGEST":
                    if job.status == "PENDING":
                        outcome = digest_service.register_event(
                            s, n, job, self._window_ms(wf), self._clock()
                        )
                        s.commit()
                        return
                    if job.status != "COMPLETED":
                        return  # master waiting on its window, or merged away
                elif job.status == "PENDING":
                    pending_delivery.append(job.id)
        for job_id in pending_delivery:
            self._deliver(job_id)
        with self._sf() as s:
            self._refresh_status(s, notification_id)
            s.commit()

    def process_due(self, now: Optional[datetime] = None) -> int:
        """Fire closed digest windows and run retries whose backoff elapsed."""
        now = now or self._clock()
        with self._sf() as s:
            windows = digest_service.due_window_ids(s, now)
            retries = list(
                s.execute(
                    select(Job.id)
                    .where(Job.status == "DELAYED", Job.step_type != "DIGEST", Job.delay_until <= now)
                    .order_by(Job.delay_until)
                    .limit(100)
                ).scalars()
            )
        for window_id in windows:
            self.fire_digest_window(window_id)
        for job_id in retries:
            self._deliver(job_id)
        return len(windows) + len(retries)

    # ── digest ────────────────────────────────────────────────────────────────
    @staticmethod
    def _window_ms(wf: Workflow) -> int:
        if settings.DIGEST_WINDOW_MS_OVERRIDE is not None:
            return settings.DIGEST_WINDOW_MS_OVERRIDE
        for step in wf.steps:
            if step.get("type") == "DIGEST":
                return int(step.get("durationMs", digest_service.DEFAULT_WINDOW_MS))
        return digest_service.DEFAULT_WINDOW_MS

    def fire_digest_window(self, window_id: str) -> None:
        with self._sf() as s:
            window = digest_service.claim_window(s, window_id)
            if window is None:
                return  # another worker already owns the expiry
            master_job_id, workflow_id = window.master_job_id, window.workflow_id
            starts, ends = window.created_at, window.window_ends_at
            s.commit()

        with self._sf() as s:
            wf = s.get(Workflow, workflow_id)
            events = digest_service.collect_events(s, window_id)
            summary = self.synth.synthesize(wf.name, events)
            job = s.get(Job, master_job_id)
            job.digest_metadata = {
                "windowId": window_id,
                "windowStart": starts.isoformat(),
                "windowEnd": ends.isoformat(),
                "eventCount": len(events),
                "events": events,
                "summary": summary,
            }
            job.status = "COMPLETED"
            window = s.get(DigestWindow, window_id)
            window.status = "DISPATCHED"
            notification_id = job.notification_id
            s.commit()
        logger.info(
            f"digest dispatched [window={window_id}, events={len(events)}, "
            f"summary={summary['source']}, notification={notification_id}]"
        )
        self.run_notification(notification_id)

    # ── delivery ──────────────────────────────────────────────────────────────
    def _deliver(self, job_id: str) -> None:
        now = self._clock()
        with self._sf() as s:
            claimed = s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(("PENDING", "DELAYED")))
                .values(status="RUNNING", attempts=Job.attempts + 1, delay_until=None)
                .returning(Job.id)
            ).scalar()
            if not claimed:
                s.rollback()
                return
            job = s.get(Job, job_id, populate_existing=True)
            n = s.get(Notification, job.notification_id)
            wf = s.get(Workflow, n.workflow_id)
            sub = s.execute(select(Subscriber).where(Subscriber.subscriber_id == n.subscriber_id)).scalar()
            channel = "EMAIL" if job.step_type == "EMAIL" else "IN_APP"

            # Phase 5: preference filtering happens before anything is built or sent.
            if not wf.is_critical and not is_channel_enabled(s, n.subscriber_id, n.workflow_id, job.step_type):
                self._skip(s, job, n, "SUBSCRIBER_PREFERENCE")
                return
            if channel == "EMAIL" and not (sub and sub.email):
                self._skip(s, job, n, "CONDITIONS_UNMET", error="subscriber has no email address")
                return

            digest_job = s.execute(
                select(Job).where(
                    Job.notification_id == n.id, Job.step_type == "DIGEST", Job.status == "COMPLETED"
                )
            ).scalar()
            if digest_job and digest_job.digest_metadata:
                meta = digest_job.digest_metadata
                subject, body = content.digest_content(wf.name, meta["events"], meta["summary"])
                ai_summary: Optional[Dict[str, Any]] = meta["summary"]
            else:
                subject, body = content.single_content(wf.name, n.payload)
                ai_summary = None

            key = dedup_key(n.subscriber_id, n.workflow_id, job.step_type, n.transaction_id)
            s.execute(
                pg_insert(Message)
                .values(
                    id=str(uuid.uuid4()), jobId=job.id, subscriberId=n.subscriber_id, channel=channel,
                    status="PENDING", subject=subject, content=body, aiSummary=ai_summary,
                    deduplicationKey=key, read=False, seen=False, createdAt=now,
                )
                .on_conflict_do_nothing(index_elements=["deduplicationKey"])
            )
            msg = s.execute(select(Message).where(Message.deduplication_key == key)).scalar()
            ids = f"[job={job.id}, message={msg.id}, notification={n.id}, subscriber={n.subscriber_id}]"

            if msg.status == "SENT":  # already delivered by an earlier attempt/worker
                job.status = "COMPLETED"
                self._refresh_status(s, n.id)
                s.commit()
                logger.info(f"delivery already completed, nothing to send {ids}")
                return

            if channel == "IN_APP":
                msg.status, msg.delivered_at = "SENT", now
                job.status = "COMPLETED"
                self._refresh_status(s, n.id)
                s.commit()
                logger.info(f"in-app delivered {ids}")
                return

            to, attempt, max_attempts, notification_id = sub.email, job.attempts, job.max_attempts, n.id
            msg_id = msg.id
            s.commit()

        logger.info(f"delivery attempted {ids} attempt={attempt}/{max_attempts}")
        try:
            provider_id = self.email.send(to=to, subject=subject, body=body, idempotency_key=key)
            error: Optional[EmailSendError] = None
        except EmailSendError as err:
            provider_id, error = None, err
        except Exception as err:  # unexpected provider bug: treat as retryable
            provider_id, error = None, EmailSendError(f"unexpected {type(err).__name__}", retryable=True)

        with self._sf() as s:
            job = s.get(Job, job_id, populate_existing=True)
            msg = s.get(Message, msg_id, populate_existing=True)
            if error is None:
                msg.status, msg.provider_message_id, msg.delivered_at = "SENT", provider_id, self._clock()
                job.status, job.error = "COMPLETED", None
                logger.info(f"delivery succeeded {ids} provider_id={provider_id}")
            elif error.retryable and attempt < max_attempts:
                delay = backoff_seconds(attempt)
                job.status, job.error = "DELAYED", str(error)[:500]
                job.delay_until = self._clock() + timedelta(seconds=delay)
                logger.warning(f"delivery failed {ids}: {error}; retry scheduled in {delay}s")
            else:
                job.status, job.error = "FAILED", str(error)[:500]
                msg.status = "FAILED"
                logger.error(f"delivery failed permanently {ids}: {error}")
            self._refresh_status(s, notification_id)
            s.commit()

    def _skip(self, s: Session, job: Job, n: Notification, reason: str, error: Optional[str] = None) -> None:
        job.status, job.skip_reason, job.attempts, job.error = "SKIPPED", reason, 0, error
        self._refresh_status(s, n.id)
        s.commit()
        logger.info(f"step skipped [job={job.id}, step={job.step_type}, reason={reason}, notification={n.id}]")

    @staticmethod
    def _refresh_status(s: Session, notification_id: str) -> None:
        s.flush()
        n = s.get(Notification, notification_id)
        if n is None or n.status == "MERGED":
            return
        statuses = set(s.execute(select(Job.status).where(Job.notification_id == notification_id)).scalars())
        if statuses & set(_ACTIVE):
            n.status = "PROCESSING"
        elif "FAILED" in statuses:
            n.status = "FAILED"
        else:
            n.status = "COMPLETED"
