"""
Phase 4 tests: workflow lookup, execution, recipient fan-out, and the
Notification / Job records they produce. Also covers worker integration.
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Job, Message, Notification, Subscriber, Workflow
from app.db.session import SyncSessionLocal
from app.services.queue_interface import RedisQueueProducer
from app.services.workflow_executor import (
    InvalidWorkflowError,
    WorkflowExecutor,
    WorkflowNotFoundError,
    notification_transaction_id,
)
from app.worker.worker import HeraldWorker


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _job(workflow_id="campus-bulletin", to=None, tx=None, payload=None):
    tag = _tag()
    return {
        "id": tx or f"tx-{tag}",
        "transactionId": tx or f"tx-{tag}",
        "workflowId": workflow_id,
        "to": to or f"sub-{tag}",
        "payload": payload or {"title": "Hello"},
    }


@pytest.fixture
def cleanup():
    """Collects subscriber ids / workflow ids created by a test and removes them."""
    created = {"subscribers": [], "workflows": []}
    yield created
    with SyncSessionLocal() as s:
        for sid in created["subscribers"]:
            sub = s.execute(select(Subscriber).where(Subscriber.subscriber_id == sid)).scalar()
            if sub:
                s.delete(sub)  # cascades notifications -> jobs
        s.commit()
        for wid in created["workflows"]:
            wf = s.get(Workflow, wid)
            if wf:
                s.delete(wf)
        s.commit()


def _notifications(subscriber_id):
    with SyncSessionLocal() as s:
        return s.execute(
            select(Notification).where(Notification.subscriber_id == subscriber_id)
        ).scalars().all()


def _jobs(notification_id):
    with SyncSessionLocal() as s:
        return s.execute(
            select(Job).where(Job.notification_id == notification_id).order_by(Job.created_at)
        ).scalars().all()


# 1. Workflow resolution ------------------------------------------------------

def test_valid_event_resolves_existing_workflow(cleanup):
    job = _job("campus-maintenance-alert")
    cleanup["subscribers"].append(job["to"])
    result = WorkflowExecutor().execute(job)
    assert result.workflow_id == "campus-maintenance-alert"
    assert len(result.notification_ids) == 1


# 2. Notification record ------------------------------------------------------

def test_execution_creates_expected_notification(cleanup):
    job = _job(payload={"title": "Water outage", "severity": "medium"})
    cleanup["subscribers"].append(job["to"])
    WorkflowExecutor().execute(job)

    [n] = _notifications(job["to"])
    assert n.workflow_id == "campus-bulletin"
    assert n.payload == {"title": "Water outage", "severity": "medium"}
    assert n.status == "PENDING"
    assert n.digest_window_id is None
    assert n.transaction_id == notification_transaction_id(job["transactionId"], job["to"])


# 3. Single recipient work + job chain ---------------------------------------

def test_single_recipient_creates_chained_pending_jobs(cleanup):
    job = _job("campus-bulletin")  # DIGEST -> EMAIL -> IN_APP
    cleanup["subscribers"].append(job["to"])
    WorkflowExecutor().execute(job)

    [n] = _notifications(job["to"])
    jobs = _jobs(n.id)
    assert [j.step_type for j in jobs] == ["DIGEST", "EMAIL", "IN_APP"]
    assert all(j.status == "PENDING" and j.subscriber_id == job["to"] for j in jobs)
    assert all(j.attempts == 0 and j.skip_reason is None for j in jobs)
    assert jobs[0].parent_job_id is None
    assert jobs[1].parent_job_id == jobs[0].id
    assert jobs[2].parent_job_id == jobs[1].id


def test_no_messages_are_created(cleanup):
    """Phase 4 must not deliver anything."""
    job = _job()
    cleanup["subscribers"].append(job["to"])
    WorkflowExecutor().execute(job)
    [n] = _notifications(job["to"])
    with SyncSessionLocal() as s:
        count = s.execute(
            select(Message).join(Job, Message.job_id == Job.id).where(Job.notification_id == n.id)
        ).all()
    assert count == []


# 4. Multi-recipient fan-out --------------------------------------------------

def test_multiple_recipients_fan_out(cleanup):
    tag = _tag()
    subs = [f"sub-{tag}-{i}" for i in range(3)]
    cleanup["subscribers"].extend(subs)
    job = _job("campus-maintenance-alert", to=subs + [subs[0]])  # duplicate in `to`
    result = WorkflowExecutor().execute(job)

    assert len(result.notification_ids) == 3
    assert len(result.job_ids) == 6  # 2 steps x 3 recipients
    txs = set()
    for sid in subs:
        [n] = _notifications(sid)
        txs.add(n.transaction_id)
        assert [j.step_type for j in _jobs(n.id)] == ["EMAIL", "IN_APP"]
    assert len(txs) == 3  # distinct per recipient


def test_existing_subscriber_is_reused(cleanup):
    sid = f"sub-{_tag()}"
    cleanup["subscribers"].append(sid)
    with SyncSessionLocal() as s:
        s.add(Subscriber(subscriber_id=sid, email="x@example.edu", name="X"))
        s.commit()
    WorkflowExecutor().execute(_job(to=sid))
    with SyncSessionLocal() as s:
        subs = s.execute(select(Subscriber).where(Subscriber.subscriber_id == sid)).scalars().all()
    assert len(subs) == 1 and subs[0].email == "x@example.edu"


# 5. Missing / invalid workflow ----------------------------------------------

def test_missing_workflow_raises_and_persists_nothing(cleanup):
    job = _job("does-not-exist")
    cleanup["subscribers"].append(job["to"])
    with pytest.raises(WorkflowNotFoundError):
        WorkflowExecutor().execute(job)
    assert _notifications(job["to"]) == []


def test_invalid_workflow_steps_rejected(cleanup):
    wid = f"bad-wf-{_tag()}"
    cleanup["workflows"].append(wid)
    with SyncSessionLocal() as s:
        s.add(Workflow(id=wid, name="Bad", steps=[{"type": "SMS"}]))
        s.commit()
    job = _job(wid)
    cleanup["subscribers"].append(job["to"])
    with pytest.raises(InvalidWorkflowError):
        WorkflowExecutor().execute(job)
    assert _notifications(job["to"]) == []


# 6. Idempotency --------------------------------------------------------------

def test_reprocessing_same_event_creates_no_duplicates(cleanup):
    job = _job()
    cleanup["subscribers"].append(job["to"])
    first = WorkflowExecutor().execute(job)
    second = WorkflowExecutor().execute(job)

    assert len(first.notification_ids) == 1
    assert second.notification_ids == [] and second.job_ids == []
    assert second.duplicate_subscribers == [job["to"]]
    [n] = _notifications(job["to"])
    assert len(_jobs(n.id)) == 3


def test_long_transaction_id_is_bounded_and_deterministic():
    long_tx = "t" * 128
    a = notification_transaction_id(long_tx, "student-1")
    assert len(a) <= 128
    assert a == notification_transaction_id(long_tx, "student-1")
    assert a != notification_transaction_id(long_tx, "student-2")


# 7. Worker + Redis integration ----------------------------------------------

@pytest.mark.asyncio
async def test_worker_executes_workflow_from_redis(cleanup):
    queue = f"test-p4-{_tag()}"
    producer = RedisQueueProducer()
    worker = HeraldWorker(queue_name=queue, executor=WorkflowExecutor())
    job = _job("campus-maintenance-alert")
    cleanup["subscribers"].append(job["to"])
    try:
        await producer.enqueue(queue, job)
        processed = await worker.process_one(timeout=2.0)
        assert processed is not None and processed["transactionId"] == job["transactionId"]
        assert worker.executed_count == 1
        [n] = _notifications(job["to"])
        assert len(_jobs(n.id)) == 2
    finally:
        await producer.clear(queue)
        await producer.close()
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_dead_letters_unknown_workflow():
    queue = f"test-p4-{_tag()}"
    producer = RedisQueueProducer()
    worker = HeraldWorker(queue_name=queue, executor=WorkflowExecutor())
    job = _job("does-not-exist")
    try:
        await producer.enqueue(queue, job)
        assert await worker.process_one(timeout=2.0) is None
        assert worker.failed_execution_count == 1
        client = await worker.get_client()
        dead = await client.lrange(worker.dead_letter_key, 0, -1)
        assert len(dead) == 1 and json.loads(dead[0])["data"]["workflowId"] == "does-not-exist"
        assert _notifications(job["to"]) == []
    finally:
        client = await worker.get_client()
        await client.delete(worker.dead_letter_key)
        await producer.clear(queue)
        await producer.close()
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_without_executor_keeps_phase3_behavior(cleanup):
    queue = f"test-p4-{_tag()}"
    producer = RedisQueueProducer()
    worker = HeraldWorker(queue_name=queue)
    job = _job()
    cleanup["subscribers"].append(job["to"])
    try:
        await producer.enqueue(queue, job)
        assert await worker.process_one(timeout=2.0) is not None
        assert _notifications(job["to"]) == []
    finally:
        await producer.clear(queue)
        await producer.close()
        await worker.stop()
