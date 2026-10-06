"""
Phases 5-9: preferences, digest (incl. atomic election), delivery, retry,
idempotency and AI digest. Uses MockEmailProvider / MockAIProvider only to make
failures deterministic; the real providers are covered with a patched HTTP layer.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import DigestWindow, Job, Message, Notification, Preference, Subscriber
from app.db.session import SyncSessionLocal
from app.providers.ai import (
    AIError, GroqAIProvider, DigestSynthesizer, MockAIProvider, extract_json,
)
from app.providers.email import EmailSendError, MockEmailProvider, RealEmailProvider
from app.services.job_runner import JobRunner
from app.services.workflow_executor import WorkflowExecutor

BASE = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = BASE

    def __call__(self):
        return self.now


class Env:
    def __init__(self, email=None, ai=None):
        self.clock = Clock()
        self.email = email or MockEmailProvider()
        self.ai = ai or MockAIProvider()
        self.runner = JobRunner(email_provider=self.email, synthesizer=DigestSynthesizer(self.ai), clock=self.clock)
        self.executor = WorkflowExecutor()
        self.subs = []

    def subscriber(self, email="s@example.edu", prefs=None):
        sid = f"t-{uuid.uuid4().hex[:10]}"
        self.subs.append(sid)
        with SyncSessionLocal() as s:
            s.add(Subscriber(subscriber_id=sid, email=email))
            if prefs is not None:
                s.add(Preference(subscriber_id=sid, workflow_id=None, channels=prefs))
            s.commit()
        return sid

    def trigger(self, workflow, to, payload=None, tx=None):
        tx = tx or f"tx-{uuid.uuid4().hex[:12]}"
        job = {"id": tx, "transactionId": tx, "workflowId": workflow, "to": to, "payload": payload or {"title": "T", "message": "M"}}
        result = self.executor.execute(job)
        for nid in result.notification_ids:
            self.runner.run_notification(nid)
        return result

    def later(self, minutes=10):
        self.clock.now = BASE + timedelta(minutes=minutes)
        return self.runner.process_due()


@pytest.fixture
def env():
    e = Env()
    yield e
    with SyncSessionLocal() as s:
        s.query(DigestWindow).filter(DigestWindow.subscriber_id.in_(e.subs)).delete(synchronize_session=False)
        s.query(Notification).filter(Notification.subscriber_id.in_(e.subs)).update(
            {Notification.digest_window_id: None}, synchronize_session=False)
        s.commit()
        s.query(Subscriber).filter(Subscriber.subscriber_id.in_(e.subs)).delete(synchronize_session=False)
        s.commit()


def jobs_of(sid):
    with SyncSessionLocal() as s:
        return s.execute(select(Job).where(Job.subscriber_id == sid).order_by(Job.created_at)).scalars().all()


def msgs_of(sid):
    with SyncSessionLocal() as s:
        return s.execute(select(Message).where(Message.subscriber_id == sid)).scalars().all()


def notifs_of(sid):
    with SyncSessionLocal() as s:
        return s.execute(select(Notification).where(Notification.subscriber_id == sid)).scalars().all()


def by_step(sid):
    return {j.step_type: j for j in jobs_of(sid)}


# ── Phase 5: preferences ─────────────────────────────────────────────────────

def test_preferences_enabled_delivers_both_channels(env):
    sid = env.subscriber(prefs={"email": True, "in_app": True})
    env.trigger("campus-maintenance-alert", sid)
    j = by_step(sid)
    assert j["EMAIL"].status == "COMPLETED" and j["IN_APP"].status == "COMPLETED"
    assert len(env.email.sent) == 1


def test_killer_test_2_email_disabled_in_app_still_delivered(env):
    sid = env.subscriber(prefs={"email": False, "in_app": True})
    env.trigger("campus-maintenance-alert", sid)
    j = by_step(sid)
    assert j["EMAIL"].status == "SKIPPED" and j["EMAIL"].skip_reason == "SUBSCRIBER_PREFERENCE"
    assert env.email.attempts == 0 and env.email.sent == []
    assert j["IN_APP"].status == "COMPLETED"
    [m] = msgs_of(sid)
    assert m.channel == "IN_APP" and m.status == "SENT"
    assert notifs_of(sid)[0].status == "COMPLETED"


def test_in_app_disabled_skips_in_app_only(env):
    sid = env.subscriber(prefs={"email": True, "in_app": False})
    env.trigger("campus-maintenance-alert", sid)
    j = by_step(sid)
    assert j["IN_APP"].skip_reason == "SUBSCRIBER_PREFERENCE" and j["EMAIL"].status == "COMPLETED"


def test_critical_workflow_overrides_mute(env):
    sid = env.subscriber(prefs={"email": False, "in_app": False})
    env.trigger("campus-emergency", sid)
    assert len(env.email.sent) == 1


def test_workflow_specific_preference_overrides_global(env):
    sid = env.subscriber(prefs={"email": True, "in_app": True})
    with SyncSessionLocal() as s:
        s.add(Preference(subscriber_id=sid, workflow_id="campus-maintenance-alert", channels={"email": False}))
        s.commit()
    env.trigger("campus-maintenance-alert", sid)
    assert by_step(sid)["EMAIL"].skip_reason == "SUBSCRIBER_PREFERENCE"


def test_no_email_address_is_skipped_not_failed(env):
    sid = env.subscriber(email=None)
    env.trigger("campus-maintenance-alert", sid)
    j = by_step(sid)["EMAIL"]
    assert j.status == "SKIPPED" and j.skip_reason == "CONDITIONS_UNMET"


# ── Phase 6: digest ──────────────────────────────────────────────────────────

def test_single_event_digest_waits_then_delivers_once(env):
    sid = env.subscriber()
    env.trigger("campus-bulletin", sid)
    assert by_step(sid)["DIGEST"].status == "DELAYED"
    assert env.email.sent == []  # held until the window closes
    assert env.later(1) == 0     # window (5 min) still open
    assert env.later(6) == 1
    assert by_step(sid)["DIGEST"].status == "COMPLETED"
    assert len(env.email.sent) == 1


def test_killer_test_1_ten_events_one_digest(env):
    sid = env.subscriber()
    txs = [f"kt1-{uuid.uuid4().hex[:8]}-{i}" for i in range(10)]
    for i, tx in enumerate(txs):
        env.trigger("campus-bulletin", sid, {"title": f"Update {i}", "message": f"Body {i}"}, tx=tx)
    env.later(6)

    with SyncSessionLocal() as s:
        windows = s.execute(select(DigestWindow).where(DigestWindow.subscriber_id == sid)).scalars().all()
    assert len(windows) == 1 and windows[0].event_count == 10 and windows[0].status == "DISPATCHED"

    notifs = notifs_of(sid)
    assert sum(n.status == "MERGED" for n in notifs) == 9
    assert all(n.digest_window_id == windows[0].id for n in notifs)

    digest_jobs = [j for j in jobs_of(sid) if j.step_type == "DIGEST"]
    assert sum(j.status == "COMPLETED" for j in digest_jobs) == 1  # one master
    assert sum(j.status == "MERGED" for j in digest_jobs) == 9
    master = next(j for j in digest_jobs if j.status == "COMPLETED")
    assert master.id == windows[0].master_job_id
    meta = master.digest_metadata
    assert meta["eventCount"] == 10
    assert {e["transactionId"].split(":")[0] for e in meta["events"]} == set(txs)  # identity preserved

    assert len(env.email.sent) == 1                      # one consolidated email
    in_app = [m for m in msgs_of(sid) if m.channel == "IN_APP"]
    assert len(in_app) == 1 and len(msgs_of(sid)) == 2   # one Message per channel
    assert "Update 9" in env.email.sent[0]["body"]


def test_different_subscribers_get_separate_windows(env):
    a, b = env.subscriber(), env.subscriber()
    env.trigger("campus-bulletin", [a, b])
    with SyncSessionLocal() as s:
        n = s.scalar(select(func.count()).select_from(DigestWindow).where(DigestWindow.subscriber_id.in_([a, b])))
    assert n == 2


def test_event_after_window_closes_opens_new_window(env):
    sid = env.subscriber()
    env.trigger("campus-bulletin", sid)
    env.later(6)
    env.trigger("campus-bulletin", sid)
    with SyncSessionLocal() as s:
        wins = s.execute(select(DigestWindow).where(DigestWindow.subscriber_id == sid)).scalars().all()
    assert sorted(w.status for w in wins) == ["DISPATCHED", "OPEN"]


def test_same_event_is_not_merged_twice(env):
    sid = env.subscriber()
    env.trigger("campus-bulletin", sid, tx="dup-1")
    env.trigger("campus-bulletin", sid, tx="dup-1")  # redelivered
    with SyncSessionLocal() as s:
        w = s.execute(select(DigestWindow).where(DigestWindow.subscriber_id == sid)).scalar_one()
    assert w.event_count == 1


# ── Phase 7: atomic ownership ────────────────────────────────────────────────

def test_concurrent_events_elect_exactly_one_master(env):
    sid = env.subscriber()
    n = 12
    barrier = threading.Barrier(n)

    def fire(i):
        barrier.wait()
        env.trigger("campus-bulletin", sid, {"title": f"E{i}"}, tx=f"conc-{uuid.uuid4().hex[:8]}-{i}")

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(fire, range(n)))

    with SyncSessionLocal() as s:
        wins = s.execute(select(DigestWindow).where(DigestWindow.subscriber_id == sid)).scalars().all()
    assert len(wins) == 1 and wins[0].event_count == n

    digest_jobs = [j for j in jobs_of(sid) if j.step_type == "DIGEST"]
    assert sum(j.status == "DELAYED" for j in digest_jobs) == 1   # one master
    assert sum(j.status == "MERGED" for j in digest_jobs) == n - 1
    assert sum(1 for x in notifs_of(sid) if x.status == "MERGED") == n - 1


def test_partial_unique_index_blocks_second_open_window(env):
    from sqlalchemy.exc import IntegrityError
    sid = env.subscriber()
    env.trigger("campus-bulletin", sid)
    with SyncSessionLocal() as s:
        s.add(DigestWindow(subscriber_id=sid, workflow_id="campus-bulletin", digest_key=None,
                           status="OPEN", master_job_id="x", window_ends_at=BASE))
        with pytest.raises(IntegrityError):
            s.commit()


# ── Phase 8: delivery / retry / idempotency ──────────────────────────────────

def test_killer_test_3_retry_without_duplicates():
    email = MockEmailProvider(failures=[EmailSendError("provider timeout")])
    env = Env(email=email)
    try:
        sid = env.subscriber()
        env.trigger("campus-maintenance-alert", sid)

        j = by_step(sid)["EMAIL"]
        assert j.status == "DELAYED" and j.attempts == 1 and "timeout" in j.error
        assert j.delay_until == BASE + timedelta(seconds=settings.RETRY_BASE_DELAY_SECONDS)
        [email_msg] = [m for m in msgs_of(sid) if m.channel == "EMAIL"]
        first_id = email_msg.id
        assert email_msg.status == "PENDING" and email.sent == []

        assert env.later(1) == 1  # backoff elapsed -> retry runs
        j = by_step(sid)["EMAIL"]
        assert j.status == "COMPLETED" and j.attempts == 2
        email_msgs = [m for m in msgs_of(sid) if m.channel == "EMAIL"]
        assert len(email_msgs) == 1 and email_msgs[0].id == first_id    # same Message identity
        assert email_msgs[0].status == "SENT" and email_msgs[0].provider_message_id
        assert len(email.sent) == 1 and email.attempts == 2             # one successful delivery
        assert len(notifs_of(sid)) == 1 and notifs_of(sid)[0].status == "COMPLETED"
        assert env.later(120) == 0                                      # nothing left to retry
        assert len(email.sent) == 1
    finally:
        _drop(env)


def _drop(env):
    with SyncSessionLocal() as s:
        s.query(Notification).filter(Notification.subscriber_id.in_(env.subs)).update(
            {Notification.digest_window_id: None}, synchronize_session=False)
        s.query(DigestWindow).filter(DigestWindow.subscriber_id.in_(env.subs)).delete(synchronize_session=False)
        s.query(Subscriber).filter(Subscriber.subscriber_id.in_(env.subs)).delete(synchronize_session=False)
        s.commit()


def test_backoff_is_exponential_and_max_attempts_end_in_failure():
    email = MockEmailProvider(failures=[EmailSendError("down")] * 5)
    env = Env(email=email)
    try:
        sid = env.subscriber()
        env.trigger("campus-maintenance-alert", sid)
        base = settings.RETRY_BASE_DELAY_SECONDS
        j = by_step(sid)["EMAIL"]
        assert (j.delay_until - BASE).total_seconds() == base
        env.later(1); j = by_step(sid)["EMAIL"]
        assert j.attempts == 2 and (j.delay_until - env.clock.now).total_seconds() == base * 2
        env.later(2); j = by_step(sid)["EMAIL"]
        assert j.status == "FAILED" and j.attempts == settings.DELIVERY_MAX_ATTEMPTS
        assert [m.status for m in msgs_of(sid) if m.channel == "EMAIL"] == ["FAILED"]
        assert notifs_of(sid)[0].status == "FAILED"
        assert email.sent == [] and len(notifs_of(sid)) == 1
    finally:
        _drop(env)


def test_permanent_error_is_not_retried():
    email = MockEmailProvider(failures=[EmailSendError("invalid recipient", retryable=False)])
    env = Env(email=email)
    try:
        sid = env.subscriber()
        env.trigger("campus-maintenance-alert", sid)
        j = by_step(sid)["EMAIL"]
        assert j.status == "FAILED" and j.attempts == 1 and email.attempts == 1
        # in-app is independent of the email failure
        assert by_step(sid)["IN_APP"].status == "COMPLETED"
    finally:
        _drop(env)


def test_reprocessing_never_duplicates_delivery(env):
    sid = env.subscriber()
    r = env.trigger("campus-maintenance-alert", sid, tx="idem-1")
    env.trigger("campus-maintenance-alert", sid, tx="idem-1")      # event redelivered
    env.runner.run_notification(r.notification_ids[0])              # runner re-run
    assert len(env.email.sent) == 1 and len(msgs_of(sid)) == 2 and len(notifs_of(sid)) == 1


def test_message_survives_lost_job_state(env):
    """If the job re-runs after the Message was already SENT, nothing is re-sent."""
    sid = env.subscriber()
    env.trigger("campus-maintenance-alert", sid)
    email_job = by_step(sid)["EMAIL"]
    with SyncSessionLocal() as s:
        s.get(Job, email_job.id).status = "PENDING"
        s.commit()
    env.runner._deliver(email_job.id)
    assert len(env.email.sent) == 1
    assert by_step(sid)["EMAIL"].status == "COMPLETED"


def test_real_provider_success_path(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json)
        return httpx.Response(200, json={"id": "re_123"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    p = RealEmailProvider(api_key="test-key", from_address="a@x.dev", from_name="Herald")
    assert p.send(to="u@x.edu", subject="S", body="B", idempotency_key="k1") == "re_123"
    assert seen["headers"]["Idempotency-Key"] == "k1"
    assert seen["json"]["to"] == ["u@x.edu"] and seen["json"]["from"] == "Herald <a@x.dev>"


@pytest.mark.parametrize("status,retryable", [(500, True), (429, True), (422, False), (401, False)])
def test_real_provider_error_classification(monkeypatch, status, retryable):
    monkeypatch.setattr(httpx, "post", lambda url, headers, json, timeout: httpx.Response(
        status, text="err", request=httpx.Request("POST", url)))
    with pytest.raises(EmailSendError) as e:
        RealEmailProvider(api_key="k").send(to="u@x.edu", subject="S", body="B", idempotency_key="k")
    assert e.value.retryable is retryable


def test_real_provider_without_key_is_permanent_error():
    with pytest.raises(EmailSendError) as e:
        RealEmailProvider(api_key="").send(to="u@x.edu", subject="S", body="B", idempotency_key="k")
    assert e.value.retryable is False and "not configured" in str(e.value)


# ── Phase 9: AI digest ───────────────────────────────────────────────────────

def _ten(env, sid):
    for i in range(3):
        env.trigger("campus-bulletin", sid, {"title": f"U{i}", "message": f"m{i}"})
    env.later(6)


def test_ai_digest_success_is_persisted_and_delivered():
    ai = MockAIProvider({"tldr": "Block A closes.", "action_items": ["Use Block B"]})
    env = Env(ai=ai)
    try:
        sid = env.subscriber()
        _ten(env, sid)
        meta = next(j for j in jobs_of(sid) if j.step_type == "DIGEST" and j.digest_metadata).digest_metadata
        assert meta["summary"]["source"] == "ai" and meta["summary"]["tldr"] == "Block A closes."
        assert "TL;DR: Block A closes." in env.email.sent[0]["body"] and "- Use Block B" in env.email.sent[0]["body"]
        assert all(m.ai_summary and m.ai_summary["action_items"] == ["Use Block B"] for m in msgs_of(sid))
        assert ai.calls == 1
    finally:
        _drop(env)


@pytest.mark.parametrize("ai", [
    MockAIProvider(error=AIError("rate limited")),
    MockAIProvider(response={"summary": "wrong shape"}),
    MockAIProvider(response={"tldr": "", "action_items": []}),
    MockAIProvider(response={"tldr": "x", "action_items": "not-a-list"}),
    MockAIProvider(error=RuntimeError("boom")),
])
def test_ai_failure_falls_back_and_still_delivers(ai):
    env = Env(ai=ai)
    try:
        sid = env.subscriber()
        _ten(env, sid)
        meta = next(j for j in jobs_of(sid) if j.step_type == "DIGEST" and j.digest_metadata).digest_metadata
        assert meta["summary"]["source"] == "fallback" and meta["summary"]["tldr"]
        assert len(env.email.sent) == 1
        assert [m.status for m in msgs_of(sid) if m.channel == "IN_APP"] == ["SENT"]
    finally:
        _drop(env)


def test_single_event_digest_skips_ai():
    ai = MockAIProvider()
    env = Env(ai=ai)
    try:
        sid = env.subscriber()
        env.trigger("campus-bulletin", sid)
        env.later(6)
        assert ai.calls == 0 and len(env.email.sent) == 1
    finally:
        _drop(env)


def test_extract_json_handles_fences_and_garbage():
    assert extract_json('```json\n{"tldr": "a", "action_items": []}\n```')["tldr"] == "a"
    with pytest.raises(AIError):
        extract_json("no json here")
    with pytest.raises(AIError):
        extract_json("{broken")


def test_groq_provider_parses_and_classifies(monkeypatch):
    def ok(url, headers, json, timeout):
        assert headers["Authorization"] == "Bearer k" and json["model"] == settings.AI_MODEL
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [
            {"message": {"content": '{"tldr": "t", "action_items": ["a"]}'}}]})
    monkeypatch.setattr(httpx, "post", ok)
    assert GroqAIProvider(api_key="k").summarize("wf", [{"title": "x"}])["tldr"] == "t"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, request=httpx.Request("POST", "u")))
    with pytest.raises(AIError):
        GroqAIProvider(api_key="k").summarize("wf", [])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"oops": 1}, request=httpx.Request("POST", "u")))
    with pytest.raises(AIError):
        GroqAIProvider(api_key="k").summarize("wf", [])
    with pytest.raises(AIError):
        GroqAIProvider(api_key="").summarize("wf", [])
