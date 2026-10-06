"""
Digest engine (Phases 6 + 7).

Master election is a single atomic ``INSERT ... ON CONFLICT DO NOTHING`` against
the partial unique index on digest_windows(subscriberId, workflowId, digestKey)
WHERE status='OPEN'. Exactly one concurrent inserter wins and becomes the
master; every loser merges into the winner's window with an atomic counter
UPDATE. There is no check-then-insert.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import DigestWindow, Job, Notification

logger = logging.getLogger("herald.digest")

DEFAULT_WINDOW_MS = 300_000


@dataclass
class DigestOutcome:
    role: str  # "master" | "merged" | "already"
    window_id: str


def digest_key_for(payload: Dict[str, Any]) -> Optional[str]:
    key = payload.get("digestKey")
    return str(key) if key else None


def register_event(
    session: Session,
    notification: Notification,
    digest_job: Job,
    duration_ms: int,
    now: datetime,
) -> DigestOutcome:
    """Atomically become the window master or merge into the open window."""
    if notification.digest_window_id:
        return DigestOutcome("already", notification.digest_window_id)

    key = digest_key_for(notification.payload)
    sub, wf = notification.subscriber_id, notification.workflow_id

    for _ in range(5):
        inserted = session.execute(
            pg_insert(DigestWindow)
            .values(
                id=str(uuid.uuid4()),
                subscriberId=sub,
                workflowId=wf,
                digestKey=key,
                status="OPEN",
                masterJobId=digest_job.id,
                windowEndsAt=now + timedelta(milliseconds=duration_ms),
                eventCount=1,
                createdAt=now,
            )
            .on_conflict_do_nothing(
                index_elements=["subscriberId", "workflowId", "digestKey"],
                index_where=text("status = 'OPEN'"),
            )
            .returning(DigestWindow.id, DigestWindow.window_ends_at)
        ).first()

        if inserted:
            window_id, ends_at = inserted
            notification.digest_window_id = window_id
            notification.status = "PROCESSING"
            digest_job.status = "DELAYED"
            digest_job.delay_until = ends_at
            session.flush()
            logger.info(f"digest master elected [window={window_id}, job={digest_job.id}, subscriber={sub}]")
            return DigestOutcome("master", window_id)

        merged = session.execute(
            update(DigestWindow)
            .where(
                DigestWindow.subscriber_id == sub,
                DigestWindow.workflow_id == wf,
                DigestWindow.digest_key.is_not_distinct_from(key),
                DigestWindow.status == "OPEN",
            )
            .values(event_count=DigestWindow.event_count + 1)
            .returning(DigestWindow.id)
        ).scalar()
        if merged:
            notification.digest_window_id = merged
            notification.status = "MERGED"
            session.execute(
                update(Job)
                .where(Job.notification_id == notification.id, Job.status == "PENDING")
                .values(status="MERGED")
            )
            session.flush()
            logger.info(f"digest merged [window={merged}, notification={notification.id}]")
            return DigestOutcome("merged", merged)
        # The window closed between our insert attempt and the update: retry.

    raise RuntimeError("could not elect or join a digest window")


def due_window_ids(
    session: Session, now: datetime, limit: int = 50, subscriber_ids: Optional[List[str]] = None
) -> List[str]:
    q = (
        select(DigestWindow.id)
        .where(DigestWindow.status == "OPEN", DigestWindow.window_ends_at <= now)
        .order_by(DigestWindow.window_ends_at)
        .limit(limit)
    )
    if subscriber_ids is not None:
        q = q.where(DigestWindow.subscriber_id.in_(subscriber_ids))
    return list(session.execute(q).scalars())


def claim_window(session: Session, window_id: str) -> Optional[DigestWindow]:
    """OPEN -> EXPIRED, atomically; only one caller can win."""
    row = session.execute(
        update(DigestWindow)
        .where(DigestWindow.id == window_id, DigestWindow.status == "OPEN")
        .values(status="EXPIRED")
        .returning(DigestWindow.id)
    ).scalar()
    return session.get(DigestWindow, row) if row else None


def collect_events(session: Session, window_id: str) -> List[Dict[str, Any]]:
    """Every original event in the window, preserving identity, oldest first."""
    rows = session.execute(
        select(Notification)
        .where(Notification.digest_window_id == window_id)
        .order_by(Notification.created_at, Notification.id)
    ).scalars()
    return [
        {
            "notificationId": n.id,
            "transactionId": n.transaction_id,
            "payload": n.payload,
            "receivedAt": n.created_at.isoformat(),
        }
        for n in rows
    ]
