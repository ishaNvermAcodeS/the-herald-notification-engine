"""
Phase 4 workflow orchestration.

Turns one consumed event job into persisted, subscriber-specific work:

    event job -> workflow lookup -> recipient resolution
              -> one Notification per recipient
              -> one Job per workflow step (chained via parentJobId)

Deliberately NOT done here (later phases): preference filtering, digest
grouping, delivery, retry, AI. Every Job is created ``PENDING`` so those phases
can pick the work up unchanged.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Job, Notification
from app.db.repositories.workflow_repo import get_workflow_sync
from app.db.session import SyncSessionLocal
from app.services.recipient_resolver import DefaultRecipientResolver, RecipientResolver

logger = logging.getLogger("herald.workflow")

VALID_STEP_TYPES = {"DIGEST", "EMAIL", "IN_APP"}
_MAX_TX_LEN = 128


class WorkflowNotFoundError(Exception):
    """The event references a workflow that does not exist at worker time."""


class InvalidWorkflowError(Exception):
    """The workflow exists but its step definition cannot be executed."""


@dataclass
class ExecutionResult:
    workflow_id: str
    transaction_id: str
    notification_ids: List[str] = field(default_factory=list)
    job_ids: List[str] = field(default_factory=list)
    # Recipients whose notification already existed (redelivered queue job).
    duplicate_subscribers: List[str] = field(default_factory=list)


def notification_transaction_id(transaction_id: str, subscriber_id: str) -> str:
    """
    Notification.transactionId is unique and a Notification is per subscriber,
    so a multi-recipient event needs a per-recipient key. The key is
    deterministic, which makes re-processing the same event idempotent.
    """
    key = f"{transaction_id}:{subscriber_id}"
    if len(key) <= _MAX_TX_LEN:
        return key
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"{transaction_id[:50]}:{digest[:64]}"[:_MAX_TX_LEN]


class WorkflowExecutor:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SyncSessionLocal,
        resolver: Optional[RecipientResolver] = None,
    ):
        self._session_factory = session_factory
        self._resolver = resolver or DefaultRecipientResolver()

    def execute(self, job: Dict[str, Any]) -> ExecutionResult:
        """
        Synchronous (DB-bound) execution of one validated queue job.
        Raises WorkflowNotFoundError / InvalidWorkflowError; nothing is
        persisted in that case.
        """
        workflow_id = job["workflowId"]
        transaction_id = job["transactionId"]
        result = ExecutionResult(workflow_id=workflow_id, transaction_id=transaction_id)

        with self._session_factory() as session:
            workflow = get_workflow_sync(session, workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' does not exist")
            steps = self._validated_steps(workflow.id, workflow.steps)

            try:
                recipients = self._resolver.resolve(session, job["to"])
                for subscriber_id in recipients:
                    self._fan_out_one(session, job, subscriber_id, steps, result)
                session.commit()
            except Exception:
                session.rollback()
                raise

        logger.info(
            f"Executed workflow [workflow={workflow_id}, tx={transaction_id}, "
            f"notifications={len(result.notification_ids)}, jobs={len(result.job_ids)}, "
            f"duplicates={len(result.duplicate_subscribers)}]"
        )
        return result

    @staticmethod
    def _validated_steps(workflow_id: str, steps: Any) -> List[Dict[str, Any]]:
        if not isinstance(steps, list) or not steps:
            raise InvalidWorkflowError(f"Workflow '{workflow_id}' has no steps")
        for step in steps:
            step_type = step.get("type") if isinstance(step, dict) else None
            if step_type not in VALID_STEP_TYPES:
                raise InvalidWorkflowError(
                    f"Workflow '{workflow_id}' has unsupported step type {step_type!r}"
                )
        return steps

    def _fan_out_one(
        self,
        session: Session,
        job: Dict[str, Any],
        subscriber_id: str,
        steps: List[Dict[str, Any]],
        result: ExecutionResult,
    ) -> None:
        tx = notification_transaction_id(job["transactionId"], subscriber_id)

        existing = session.execute(
            select(Notification.id).where(Notification.transaction_id == tx)
        ).scalar()
        if existing is not None:
            result.duplicate_subscribers.append(subscriber_id)
            return

        notification = Notification(
            transaction_id=tx,
            subscriber_id=subscriber_id,
            workflow_id=job["workflowId"],
            payload=job["payload"],
            status="PENDING",
        )
        try:
            # Savepoint: a concurrent worker winning the unique key must not
            # poison the rest of the fan-out.
            with session.begin_nested():
                session.add(notification)
                session.flush()
        except IntegrityError:
            result.duplicate_subscribers.append(subscriber_id)
            return

        parent_id: Optional[str] = None
        for step in steps:
            step_job = Job(
                notification_id=notification.id,
                parent_job_id=parent_id,
                subscriber_id=subscriber_id,
                step_type=step["type"],
                status="PENDING",
                max_attempts=settings.DELIVERY_MAX_ATTEMPTS,
            )
            session.add(step_job)
            session.flush()
            result.job_ids.append(step_job.id)
            parent_id = step_job.id

        result.notification_ids.append(notification.id)
