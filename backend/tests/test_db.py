"""
Phase 2 database tests.

Tests:
- PostgreSQL connection (sync + async)
- Required tables exist
- Required constraints work
- CRUD sanity for all 7 entities
- Workflow DB lookup (Phase 1 integration)
- Subscriber + Preference relationship
- Notification + Job + Message relationships
- DigestWindow fields and partial unique index
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import inspect, text

from app.db.models import (
    Subscriber,
    Preference,
    Workflow,
    DigestWindow,
    Notification,
    Job,
    Message,
)
from app.db.session import SyncSessionLocal, sync_engine
from app.db.repositories.workflow_repo import workflow_exists_sync, get_workflow_sync


# ── Helper ─────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Connection ─────────────────────────────────────────────────────────────────

def test_sync_connection():
    """PostgreSQL sync connection succeeds."""
    with sync_engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_session_creation():
    """A sync session can be created and closed cleanly."""
    with SyncSessionLocal() as session:
        result = session.execute(text("SELECT current_database()"))
        db = result.scalar()
        assert db == "the_herald"


# ── Schema / Tables ────────────────────────────────────────────────────────────

REQUIRED_TABLES = {
    "subscribers",
    "preferences",
    "workflows",
    "digest_windows",
    "notifications",
    "jobs",
    "messages",
}


def test_required_tables_exist():
    """All 7 required Herald tables are present in the database."""
    inspector = inspect(sync_engine)
    existing = set(inspector.get_table_names())
    missing = REQUIRED_TABLES - existing
    assert not missing, f"Missing tables: {missing}"


def test_subscribers_columns():
    """Subscribers table has required columns."""
    inspector = inspect(sync_engine)
    cols = {c["name"] for c in inspector.get_columns("subscribers")}
    for required in ("id", "subscriberId", "email", "name", "createdAt", "updatedAt"):
        assert required in cols, f"Column {required!r} missing from subscribers"


def test_workflows_columns():
    inspector = inspect(sync_engine)
    cols = {c["name"] for c in inspector.get_columns("workflows")}
    for required in ("id", "name", "description", "isCritical", "steps", "createdAt"):
        assert required in cols


def test_digest_windows_columns():
    inspector = inspect(sync_engine)
    cols = {c["name"] for c in inspector.get_columns("digest_windows")}
    for required in (
        "id", "subscriberId", "workflowId", "digestKey",
        "status", "masterJobId", "windowEndsAt", "eventCount", "createdAt"
    ):
        assert required in cols


def test_jobs_columns():
    inspector = inspect(sync_engine)
    cols = {c["name"] for c in inspector.get_columns("jobs")}
    for required in (
        "id", "notificationId", "parentJobId", "subscriberId",
        "stepType", "status", "skipReason", "attempts", "maxAttempts",
        "delayUntil", "error", "digestMetadata", "createdAt", "updatedAt"
    ):
        assert required in cols


def test_messages_columns():
    inspector = inspect(sync_engine)
    cols = {c["name"] for c in inspector.get_columns("messages")}
    for required in (
        "id", "jobId", "subscriberId", "channel", "status",
        "subject", "content", "aiSummary", "deduplicationKey",
        "providerMessageId", "read", "seen", "createdAt", "deliveredAt"
    ):
        assert required in cols


# ── Workflow CRUD + Phase 1 lookup ─────────────────────────────────────────────

def test_dev_workflows_seeded():
    """Dev workflows are present (seeded during Phase 2 setup)."""
    with SyncSessionLocal() as session:
        for wf_id in ("campus-maintenance-alert", "campus-bulletin", "campus-emergency"):
            wf = session.get(Workflow, wf_id)
            assert wf is not None, f"Workflow {wf_id!r} not found in DB"


def test_workflow_exists_sync_true():
    """workflow_exists_sync returns True for seeded workflows."""
    with SyncSessionLocal() as session:
        assert workflow_exists_sync(session, "campus-bulletin") is True


def test_workflow_exists_sync_false():
    """workflow_exists_sync returns False for unknown workflow."""
    with SyncSessionLocal() as session:
        assert workflow_exists_sync(session, "nonexistent-workflow-xyz") is False


def test_get_workflow_sync():
    """get_workflow_sync returns the workflow with correct fields."""
    with SyncSessionLocal() as session:
        wf = get_workflow_sync(session, "campus-emergency")
        assert wf is not None
        assert wf.name == "Campus Emergency Alert"
        assert wf.is_critical is True


def test_create_custom_workflow():
    """A custom workflow can be persisted and retrieved."""
    wf_id = f"test-wf-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        wf = Workflow(
            id=wf_id,
            name="Test Workflow",
            steps=[{"type": "EMAIL"}],
        )
        session.add(wf)
        session.commit()

    with SyncSessionLocal() as session:
        found = session.get(Workflow, wf_id)
        assert found is not None
        assert found.name == "Test Workflow"
        # Cleanup
        session.delete(found)
        session.commit()


# ── Subscriber CRUD ────────────────────────────────────────────────────────────

def test_create_subscriber():
    """A subscriber can be persisted and retrieved."""
    sub_id = f"test-student-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        sub = Subscriber(
            subscriber_id=sub_id,
            email=f"{sub_id}@campus.edu",
            name="Test Student",
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)
        assert sub.id is not None

    with SyncSessionLocal() as session:
        found = session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Subscriber).where(
                Subscriber.subscriber_id == sub_id
            )
        ).scalar_one_or_none()
        assert found is not None
        assert found.email == f"{sub_id}@campus.edu"
        session.delete(found)
        session.commit()


def test_subscriber_unique_constraint():
    """Two subscribers with the same subscriberId raises an IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    sub_id = f"dup-student-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        session.add(Subscriber(subscriber_id=sub_id, email="a@campus.edu"))
        session.commit()

    with SyncSessionLocal() as session:
        session.add(Subscriber(subscriber_id=sub_id, email="b@campus.edu"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # Cleanup
    with SyncSessionLocal() as session:
        found = session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Subscriber).where(
                Subscriber.subscriber_id == sub_id
            )
        ).scalar_one_or_none()
        if found:
            session.delete(found)
            session.commit()


# ── Preference ─────────────────────────────────────────────────────────────────

def test_create_preference():
    """A preference can be created and references the correct subscriber."""
    from sqlalchemy import select

    sub_id = f"pref-student-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        sub = Subscriber(subscriber_id=sub_id, email=f"{sub_id}@campus.edu")
        session.add(sub)
        session.flush()

        pref = Preference(
            subscriber_id=sub_id,
            workflow_id=None,
            channels={"email": False, "in_app": True},
        )
        session.add(pref)
        session.commit()

    with SyncSessionLocal() as session:
        pref = session.execute(
            select(Preference).where(Preference.subscriber_id == sub_id)
        ).scalar_one_or_none()
        assert pref is not None
        assert pref.channels == {"email": False, "in_app": True}

        # Cleanup
        session.delete(pref)
        sub = session.execute(
            select(Subscriber).where(Subscriber.subscriber_id == sub_id)
        ).scalar_one()
        session.delete(sub)
        session.commit()


# ── Notification + Job + Message chain ────────────────────────────────────────

def test_notification_job_message_chain():
    """Notification → Job → Message relationship chain persists correctly."""
    from sqlalchemy import select

    sub_id = f"chain-student-{_uid()[:8]}"
    tx_id = f"tx-{_uid()[:16]}"

    with SyncSessionLocal() as session:
        # Subscriber
        sub = Subscriber(subscriber_id=sub_id)
        session.add(sub)
        session.flush()

        # Notification
        notif = Notification(
            transaction_id=tx_id,
            subscriber_id=sub_id,
            workflow_id="campus-maintenance-alert",
            payload={"title": "Test Alert"},
            status="PENDING",
        )
        session.add(notif)
        session.flush()

        # Job
        job = Job(
            notification_id=notif.id,
            subscriber_id=sub_id,
            step_type="EMAIL",
            status="PENDING",
        )
        session.add(job)
        session.flush()

        # Message
        msg = Message(
            job_id=job.id,
            subscriber_id=sub_id,
            channel="EMAIL",
            status="PENDING",
            content="<p>Test Alert</p>",
            deduplication_key=f"dedup-{tx_id}-EMAIL",
        )
        session.add(msg)
        session.commit()

    with SyncSessionLocal() as session:
        fetched_notif = session.execute(
            select(Notification).where(Notification.transaction_id == tx_id)
        ).scalar_one()
        assert fetched_notif.workflow_id == "campus-maintenance-alert"

        fetched_job = session.execute(
            select(Job).where(Job.notification_id == fetched_notif.id)
        ).scalar_one()
        assert fetched_job.step_type == "EMAIL"

        fetched_msg = session.execute(
            select(Message).where(Message.job_id == fetched_job.id)
        ).scalar_one()
        assert fetched_msg.channel == "EMAIL"
        assert fetched_msg.deduplication_key == f"dedup-{tx_id}-EMAIL"

        # Cleanup
        session.delete(fetched_msg)
        session.delete(fetched_job)
        session.delete(fetched_notif)
        sub = session.execute(
            select(Subscriber).where(Subscriber.subscriber_id == sub_id)
        ).scalar_one()
        session.delete(sub)
        session.commit()


# ── DigestWindow ───────────────────────────────────────────────────────────────

def test_digest_window_create():
    """DigestWindow can be created with all documented fields."""
    from sqlalchemy import select

    sub_id = f"digest-student-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        dw = DigestWindow(
            subscriber_id=sub_id,
            workflow_id="campus-bulletin",
            digest_key=None,
            status="OPEN",
            master_job_id=_uid(),
            window_ends_at=_now() + timedelta(minutes=5),
            event_count=1,
        )
        session.add(dw)
        session.commit()
        dw_id = dw.id

    with SyncSessionLocal() as session:
        found = session.get(DigestWindow, dw_id)
        assert found is not None
        assert found.status == "OPEN"
        assert found.event_count == 1
        session.delete(found)
        session.commit()


def test_digest_window_open_uniqueness():
    """Only one OPEN DigestWindow may exist per (subscriberId, workflowId, digestKey)."""
    from sqlalchemy.exc import IntegrityError

    sub_id = f"dw-unique-{_uid()[:8]}"
    master_job_id = _uid()

    with SyncSessionLocal() as session:
        session.add(DigestWindow(
            subscriber_id=sub_id,
            workflow_id="campus-bulletin",
            digest_key=None,
            status="OPEN",
            master_job_id=master_job_id,
            window_ends_at=_now() + timedelta(minutes=5),
        ))
        session.commit()

    # Second OPEN window for same (subscriber, workflow, digestKey=None) → must fail
    with SyncSessionLocal() as session:
        session.add(DigestWindow(
            subscriber_id=sub_id,
            workflow_id="campus-bulletin",
            digest_key=None,
            status="OPEN",
            master_job_id=_uid(),
            window_ends_at=_now() + timedelta(minutes=5),
        ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # Cleanup
    from sqlalchemy import select, delete
    with SyncSessionLocal() as session:
        session.execute(
            delete(DigestWindow).where(DigestWindow.subscriber_id == sub_id)
        )
        session.commit()


def test_digest_window_two_open_allowed_different_workflows():
    """Two OPEN windows for different workflows on same subscriber are allowed."""
    from sqlalchemy import delete

    sub_id = f"dw-multi-wf-{_uid()[:8]}"
    with SyncSessionLocal() as session:
        session.add(DigestWindow(
            subscriber_id=sub_id,
            workflow_id="campus-bulletin",
            status="OPEN",
            master_job_id=_uid(),
            window_ends_at=_now() + timedelta(minutes=5),
        ))
        session.add(DigestWindow(
            subscriber_id=sub_id,
            workflow_id="campus-maintenance-alert",
            status="OPEN",
            master_job_id=_uid(),
            window_ends_at=_now() + timedelta(minutes=5),
        ))
        session.commit()  # Must NOT raise

    # Cleanup
    with SyncSessionLocal() as session:
        session.execute(
            delete(DigestWindow).where(DigestWindow.subscriber_id == sub_id)
        )
        session.commit()


# ── Message deduplication key uniqueness ──────────────────────────────────────

def test_message_deduplication_key_unique():
    """Two messages with the same deduplication key raise IntegrityError."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy import select, delete

    sub_id = f"dedup-student-{_uid()[:8]}"
    tx_id = _uid()
    dedup_key = f"dedup-{tx_id}"

    with SyncSessionLocal() as session:
        sub = Subscriber(subscriber_id=sub_id)
        session.add(sub)
        notif = Notification(
            transaction_id=tx_id,
            subscriber_id=sub_id,
            workflow_id="campus-maintenance-alert",
            payload={},
            status="PENDING",
        )
        session.add(notif)
        session.flush()
        job = Job(notification_id=notif.id, subscriber_id=sub_id, step_type="IN_APP", status="PENDING")
        session.add(job)
        session.flush()
        msg = Message(
            job_id=job.id, subscriber_id=sub_id,
            channel="IN_APP", status="PENDING",
            content="Test", deduplication_key=dedup_key,
        )
        session.add(msg)
        session.commit()
        job_id = job.id

    # Try inserting another message with the same dedup key via a different job
    with SyncSessionLocal() as session:
        tx_id2 = _uid()
        notif2 = Notification(
            transaction_id=tx_id2, subscriber_id=sub_id,
            workflow_id="campus-maintenance-alert", payload={}, status="PENDING",
        )
        session.add(notif2)
        session.flush()
        job2 = Job(notification_id=notif2.id, subscriber_id=sub_id, step_type="IN_APP", status="PENDING")
        session.add(job2)
        session.flush()
        msg2 = Message(
            job_id=job2.id, subscriber_id=sub_id,
            channel="IN_APP", status="PENDING",
            content="Duplicate", deduplication_key=dedup_key,
        )
        session.add(msg2)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # Cleanup
    with SyncSessionLocal() as session:
        session.execute(delete(Message).where(Message.deduplication_key == dedup_key))
        session.execute(delete(Job).where(Job.subscriber_id == sub_id))
        session.execute(delete(Notification).where(Notification.subscriber_id == sub_id))
        found_sub = session.execute(
            select(Subscriber).where(Subscriber.subscriber_id == sub_id)
        ).scalar_one_or_none()
        if found_sub:
            session.delete(found_sub)
        session.commit()
