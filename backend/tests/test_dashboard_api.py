"""Phase 10 backend: preferences, inbox and admin APIs, end-to-end through the HTTP layer."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.models import DigestWindow, Notification, Subscriber
from app.db.session import SyncSessionLocal
from app.main import app
from app.providers.ai import DigestSynthesizer, MockAIProvider
from app.providers.email import MockEmailProvider
from app.services.job_runner import JobRunner
from app.services.workflow_executor import WorkflowExecutor

client = TestClient(app)
H = {"Authorization": f"Bearer {settings.API_KEY}"}


@pytest.fixture
def sid():
    s = f"api-{uuid.uuid4().hex[:8]}"
    yield s
    with SyncSessionLocal() as db:
        db.query(DigestWindow).filter(DigestWindow.subscriber_id == s).delete()
        db.query(Notification).filter(Notification.subscriber_id == s).update({Notification.digest_window_id: None})
        db.query(Subscriber).filter(Subscriber.subscriber_id == s).delete()
        db.commit()


def _run(workflow, sid, payload, tx=None):
    tx = tx or f"tx-{uuid.uuid4().hex[:8]}"
    runner = JobRunner(email_provider=MockEmailProvider(), synthesizer=DigestSynthesizer(MockAIProvider()))
    r = WorkflowExecutor().execute({"id": tx, "transactionId": tx, "workflowId": workflow, "to": sid, "payload": payload})
    for n in r.notification_ids:
        runner.run_notification(n)


def test_auth_required():
    assert client.get("/v1/admin/overview").status_code == 401
    assert client.get("/v1/subscribers").status_code == 401


def test_subscriber_and_preference_lifecycle(sid):
    assert client.get(f"/v1/subscribers/{sid}/preferences", headers=H).status_code == 404
    assert client.put(f"/v1/subscribers/{sid}/preferences", headers=H, json={"email": False}).status_code == 404
    r = client.put(f"/v1/subscribers/{sid}", headers=H, json={"email": "a@b.edu", "name": "Alice"})
    assert r.status_code == 200 and r.json()["email"] == "a@b.edu" and r.json()["channels"] == {"email": True, "in_app": True}
    r = client.put(f"/v1/subscribers/{sid}/preferences", headers=H, json={"email": False})
    assert r.json()["channels"] == {"email": False, "in_app": True}
    r = client.put(f"/v1/subscribers/{sid}/preferences", headers=H, json={"in_app": False})
    assert r.json()["channels"] == {"email": False, "in_app": False}   # merges, keeps email=false
    assert client.get(f"/v1/subscribers/{sid}/preferences", headers=H).json()["channels"]["email"] is False
    assert client.put(f"/v1/subscribers/{sid}/preferences", headers=H, json={}).status_code == 400
    assert client.put(f"/v1/subscribers/{sid}", headers=H, json={"email": "nope"}).status_code == 400
    assert sid in [s["subscriberId"] for s in client.get("/v1/subscribers", headers=H).json()]


def test_inbox_read_unread_flow(sid):
    client.put(f"/v1/subscribers/{sid}", headers=H, json={"email": "a@b.edu"})
    _run("campus-maintenance-alert", sid, {"title": "Water off", "message": "Until noon"})
    _run("campus-maintenance-alert", sid, {"title": "Water on", "message": "Back"})
    inbox = client.get("/v1/notifications", params={"subscriberId": sid}, headers=H).json()
    assert inbox["unreadCount"] == 2 and len(inbox["items"]) == 2
    item = inbox["items"][0]
    assert item["title"] == "Water on" and item["body"] == "Back" and item["workflowId"] == "campus-maintenance-alert"
    assert len(client.get("/v1/notifications/unread", params={"subscriberId": sid}, headers=H).json()["items"]) == 2

    r = client.post(f"/v1/notifications/{item['id']}/read", params={"subscriberId": sid}, headers=H)
    assert r.status_code == 200
    unread = client.get("/v1/notifications/unread", params={"subscriberId": sid}, headers=H).json()
    assert unread["unreadCount"] == 1 and len(unread["items"]) == 1
    all_ = client.get("/v1/notifications", params={"subscriberId": sid}, headers=H).json()
    assert len(all_["items"]) == 2 and sum(i["read"] for i in all_["items"]) == 1
    # ownership: another subscriber cannot mark it
    assert client.post(f"/v1/notifications/{item['id']}/read", params={"subscriberId": "someone-else"}, headers=H).status_code == 404


def test_admin_views(sid):
    client.put(f"/v1/subscribers/{sid}", headers=H, json={"email": "a@b.edu"})
    _run("campus-bulletin", sid, {"title": "A"})
    _run("campus-bulletin", sid, {"title": "B"})
    ov = client.get("/v1/admin/overview", headers=H).json()
    assert ov["totalNotifications"] >= 2 and ov["digestCount"] >= 1 and "retrying" in ov and "delivered" in ov

    notes = [n for n in client.get("/v1/admin/notifications?limit=200", headers=H).json() if n["subscriberId"] == sid]
    assert len(notes) == 2 and sorted(n["status"] for n in notes) == ["MERGED", "PROCESSING"]
    assert {j["stepType"] for n in notes for j in n["jobs"]} == {"DIGEST", "EMAIL", "IN_APP"}

    [d] = [d for d in client.get("/v1/admin/digests", headers=H).json() if d["subscriberId"] == sid]
    assert d["status"] == "OPEN" and d["eventCount"] == 2 and len(d["events"]) == 2
    assert client.get("/v1/admin/jobs?status=delayed", headers=H).status_code == 200


def test_trigger_endpoint_still_accepts_events(sid):
    r = client.post("/v1/events/trigger", headers=H,
                    json={"workflowId": "campus-bulletin", "to": sid, "payload": {"title": "x"}})
    assert r.status_code == 202
    import redis
    redis.Redis.from_url(settings.REDIS_URL).delete("herald:queue:workflow-jobs")  # don't leave test events queued
