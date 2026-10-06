import asyncio
import json
import uuid
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.queue_interface import RedisQueueProducer
from app.worker.worker import HeraldWorker, MalformedJobError

client = TestClient(app)
AUTH_HEADERS = {"Authorization": f"Bearer {settings.API_KEY}"}


# ---------------------------------------------------------------------------
# Redis Configuration & Connectivity
# ---------------------------------------------------------------------------

def test_redis_config_loaded():
    """Verify REDIS_URL is configured and starts with redis://."""
    assert settings.REDIS_URL is not None
    assert settings.REDIS_URL.startswith("redis://")


@pytest.mark.asyncio
async def test_redis_async_connection():
    """Verify async Redis client connects and responds to PING."""
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.REDIS_URL)
    pong = await r.ping()
    assert pong is True
    await r.aclose()


# ---------------------------------------------------------------------------
# Queue Producer Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_queue_producer_enqueue():
    """Verify RedisQueueProducer enqueues a valid envelope to Redis."""
    producer = RedisQueueProducer()
    test_queue = f"test-queue-{uuid.uuid4().hex[:8]}"

    job_data = {
        "transactionId": f"tx-test-{uuid.uuid4().hex[:8]}",
        "workflowId": "campus-maintenance-alert",
        "to": "student-test-1",
        "payload": {"title": "Test Title"},
    }

    try:
        job_id = await producer.enqueue(test_queue, job_data)
        assert job_id == job_data["transactionId"]

        count = await producer.count(test_queue)
        assert count == 1

        items = await producer.peek(test_queue)
        assert len(items) == 1
        envelope = items[0]
        assert envelope["id"] == job_id
        assert envelope["queue"] == test_queue
        assert envelope["data"]["transactionId"] == job_data["transactionId"]
    finally:
        await producer.clear(test_queue)
        await producer.close()


@pytest.mark.asyncio
async def test_redis_queue_producer_enqueue_batch():
    """Verify RedisQueueProducer enqueues multiple items in a batch."""
    producer = RedisQueueProducer()
    test_queue = f"test-batch-{uuid.uuid4().hex[:8]}"

    batch_data = [
        {
            "transactionId": f"tx-batch-{i}",
            "workflowId": "campus-bulletin",
            "to": f"student-{i}",
            "payload": {"index": i},
        }
        for i in range(5)
    ]

    try:
        job_ids = await producer.enqueue_batch(test_queue, batch_data)
        assert len(job_ids) == 5

        count = await producer.count(test_queue)
        assert count == 5
    finally:
        await producer.clear(test_queue)
        await producer.close()


# ---------------------------------------------------------------------------
# Worker Deserialization, Validation, & Consumption Tests
# ---------------------------------------------------------------------------

def test_worker_parse_and_validate_valid_envelope():
    """Worker parses and validates standard enveloped message."""
    worker = HeraldWorker()
    raw = json.dumps({
        "id": "job-123",
        "queue": "workflow-jobs",
        "data": {
            "transactionId": "tx-123",
            "workflowId": "campus-maintenance-alert",
            "to": "student-99",
            "payload": {"message": "hello"},
        },
        "enqueuedAt": 123456789.0,
    })

    job = worker.parse_and_validate(raw)
    assert job["id"] == "job-123"
    assert job["transactionId"] == "tx-123"
    assert job["workflowId"] == "campus-maintenance-alert"
    assert job["to"] == "student-99"
    assert job["payload"] == {"message": "hello"}


def test_worker_parse_and_validate_raw_dict():
    """Worker parses and validates direct un-enveloped dict."""
    worker = HeraldWorker()
    raw = json.dumps({
        "transactionId": "tx-raw",
        "workflowId": "campus-emergency",
        "to": ["student-1", "student-2"],
        "payload": {"alert": "weather"},
    })

    job = worker.parse_and_validate(raw)
    assert job["transactionId"] == "tx-raw"
    assert job["workflowId"] == "campus-emergency"
    assert job["to"] == ["student-1", "student-2"]


@pytest.mark.parametrize("invalid_raw", [
    "not-json-at-all",
    json.dumps(["not", "a", "dict"]),
    json.dumps({"workflowId": "campus-bulletin", "to": "s1", "payload": {}}),  # missing transactionId
    json.dumps({"transactionId": "tx-1", "to": "s1", "payload": {}}),          # missing workflowId
    json.dumps({"transactionId": "tx-1", "workflowId": "wf", "payload": {}}),  # missing to
    json.dumps({"transactionId": "tx-1", "workflowId": "wf", "to": []}),       # empty to list
    json.dumps({"transactionId": "tx-1", "workflowId": "wf", "to": "s1"}),     # missing payload
    json.dumps({"transactionId": "tx-1", "workflowId": "wf", "to": "s1", "payload": "not-a-dict"}),
])
def test_worker_parse_and_validate_malformed_raises(invalid_raw):
    """Worker strictly detects and rejects various malformed inputs."""
    worker = HeraldWorker()
    with pytest.raises(MalformedJobError):
        worker.parse_and_validate(invalid_raw)


@pytest.mark.asyncio
async def test_worker_process_one_success():
    """Worker successfully consumes an event and removes it from Redis."""
    q_name = f"test-proc-{uuid.uuid4().hex[:8]}"
    producer = RedisQueueProducer()
    worker = HeraldWorker(queue_name=q_name)

    tx_id = f"tx-proc-{uuid.uuid4().hex[:8]}"
    job_data = {
        "transactionId": tx_id,
        "workflowId": "campus-emergency",
        "to": "student-admin",
        "payload": {"code": "RED"},
    }

    try:
        await producer.enqueue(q_name, job_data)
        assert await producer.count(q_name) == 1

        processed = await worker.process_one(timeout=1.0)
        assert processed is not None
        assert processed["transactionId"] == tx_id
        assert processed["workflowId"] == "campus-emergency"
        assert worker.consumed_count == 1
        assert worker.malformed_count == 0

        # Verify job was removed from queue (acknowledged)
        assert await producer.count(q_name) == 0
    finally:
        await producer.clear(q_name)
        await producer.close()
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_malformed_job_routed_to_dead_letter_and_does_not_crash():
    """Worker survives malformed jobs, routes them to dead-letter queue, and continues."""
    import redis.asyncio as aioredis
    q_name = f"test-malformed-{uuid.uuid4().hex[:8]}"
    dl_key = f"herald:queue:{q_name}:dead-letter"
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    worker = HeraldWorker(queue_name=q_name)

    try:
        # Push 1 corrupt non-JSON message
        await r.rpush(f"herald:queue:{q_name}", "GARBAGE_PAYLOAD_NOT_JSON")
        # Push 1 invalid schema JSON message
        await r.rpush(f"herald:queue:{q_name}", json.dumps({"incomplete": True}))
        # Push 1 valid message
        valid_tx = f"tx-valid-after-bad-{uuid.uuid4().hex[:8]}"
        await r.rpush(
            f"herald:queue:{q_name}",
            json.dumps({
                "transactionId": valid_tx,
                "workflowId": "campus-bulletin",
                "to": "student-500",
                "payload": {"news": "good news"},
            }),
        )

        # Process first malformed item
        res1 = await worker.process_one(timeout=1.0)
        assert res1 is None
        assert worker.malformed_count == 1

        # Process second malformed item
        res2 = await worker.process_one(timeout=1.0)
        assert res2 is None
        assert worker.malformed_count == 2

        # Process valid item: worker must still be running and successfully process it
        res3 = await worker.process_one(timeout=1.0)
        assert res3 is not None
        assert res3["transactionId"] == valid_tx
        assert worker.consumed_count == 1

        # Dead letter queue should contain the 2 malformed messages
        dl_count = await r.llen(dl_key)
        assert dl_count == 2
        dl_items = await r.lrange(dl_key, 0, -1)
        assert "GARBAGE_PAYLOAD_NOT_JSON" in dl_items
    finally:
        await r.delete(f"herald:queue:{q_name}")
        await r.delete(dl_key)
        await r.aclose()
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_empty_queue_timeout_returns_none():
    """Worker returns None safely when queue is empty."""
    q_name = f"test-empty-{uuid.uuid4().hex[:8]}"
    worker = HeraldWorker(queue_name=q_name)
    try:
        res = await worker.process_one(timeout=0.1)
        assert res is None
    finally:
        await worker.stop()


# ---------------------------------------------------------------------------
# End-to-End API → Redis → Worker Runtime Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_api_to_redis_to_worker():
    """
    Demonstrates full Phase 3 runtime flow:
    Client POST /v1/events/trigger -> API Enqueues to Redis -> Worker Consumes.
    """
    producer = RedisQueueProducer()
    worker = HeraldWorker(queue_name="workflow-jobs")
    await producer.clear("workflow-jobs")

    tx_id = f"tx-e2e-{uuid.uuid4().hex[:8]}"
    payload = {
        "workflowId": "campus-maintenance-alert",
        "to": "student-4200",
        "payload": {
            "title": "Power Maintenance in Library",
            "building": "Main Library",
            "startsAt": "18:00",
        },
        "transactionId": tx_id,
    }

    # Step 1: Client triggers event via API
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True
    assert data["transactionId"] == tx_id
    assert data["workflowId"] == "campus-maintenance-alert"

    # Step 2: Verify event entered Redis queue
    # The event should be in herald:queue:workflow-jobs
    queue_len = await producer.count("workflow-jobs")
    assert queue_len >= 1

    # Step 3: Worker consumes the event
    consumed_job = None
    # Poll up to 5 times to consume the specific job
    for _ in range(5):
        job = await worker.process_one(timeout=1.0)
        if job and job.get("transactionId") == tx_id:
            consumed_job = job
            break

    assert consumed_job is not None, f"Worker did not consume job {tx_id}"
    assert consumed_job["transactionId"] == tx_id
    assert consumed_job["workflowId"] == "campus-maintenance-alert"
    assert consumed_job["to"] == "student-4200"
    assert consumed_job["payload"]["building"] == "Main Library"

    await producer.close()
    await worker.stop()
