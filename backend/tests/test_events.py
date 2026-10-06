import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings

client = TestClient(app)
AUTH_HEADERS = {"Authorization": f"Bearer {settings.API_KEY}"}
API_KEY_HEADERS = {"x-api-key": settings.API_KEY}


# ---------------------------------------------------------
# Authentication Tests
# ---------------------------------------------------------

def test_trigger_without_auth_fails():
    response = client.post(
        "/v1/events/trigger",
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "student-10492",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 401
    assert "Invalid or missing API key" in response.json()["detail"]


def test_trigger_with_invalid_auth_fails():
    response = client.post(
        "/v1/events/trigger",
        headers={"Authorization": "Bearer wrong-key"},
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "student-10492",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 401


def test_trigger_with_x_api_key_header_succeeds():
    response = client.post(
        "/v1/events/trigger",
        headers=API_KEY_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "student-10492",
            "payload": {"title": "Water Outage"},
            "transactionId": "tx-x-api-key-test",
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True
    assert data["status"] == "processed"


# ---------------------------------------------------------
# Validation & Error Tests
# ---------------------------------------------------------

def test_missing_workflow_id_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "to": "student-10492",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Bad Request"


def test_missing_to_field_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 400


def test_missing_payload_field_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "student-10492",
        },
    )
    assert response.status_code == 400


def test_invalid_payload_format_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "student-10492",
            "payload": "this-is-not-a-json-object",
        },
    )
    assert response.status_code == 400


def test_empty_to_string_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": "   ",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 400


def test_empty_to_list_returns_400():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": [],
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 400


def test_to_list_exceeding_100_recipients_returns_400():
    recipients = [f"student-{i}" for i in range(101)]
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "campus-maintenance-alert",
            "to": recipients,
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 400


def test_non_existent_workflow_returns_404():
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json={
            "workflowId": "non-existent-workflow-xyz",
            "to": "student-10492",
            "payload": {"title": "Water Outage"},
        },
    )
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_db_persisted_workflow_trigger():
    """Event trigger succeeds for a custom workflow persisted directly in the database."""
    import uuid
    from app.db.models import Workflow
    from app.db.session import SyncSessionLocal

    custom_wf_id = f"custom-dept-alert-{uuid.uuid4().hex[:8]}"
    with SyncSessionLocal() as session:
        session.add(
            Workflow(
                id=custom_wf_id,
                name="Custom Dept Alert",
                steps=[{"type": "IN_APP"}],
            )
        )
        session.commit()

    try:
        response = client.post(
            "/v1/events/trigger",
            headers=AUTH_HEADERS,
            json={
                "workflowId": custom_wf_id,
                "to": "student-10492",
                "payload": {"title": "Department Seminar"},
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["acknowledged"] is True
        assert data["workflowId"] == custom_wf_id
    finally:
        with SyncSessionLocal() as session:
            wf = session.get(Workflow, custom_wf_id)
            if wf:
                session.delete(wf)
                session.commit()


# ---------------------------------------------------------
# Event Ingestion & Idempotency Tests
# ---------------------------------------------------------

def test_valid_single_event_trigger():
    payload = {
        "workflowId": "campus-maintenance-alert",
        "to": "student-10492",
        "payload": {
            "title": "Water Outage in West Dorm",
            "location": "West Dorm",
            "severity": "medium",
            "details": "Water pressure will be reduced between 2 PM and 4 PM.",
        },
        "transactionId": "tx-campus-alert-20261005-01",
    }
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True
    assert data["status"] == "processed"
    assert data["transactionId"] == "tx-campus-alert-20261005-01"
    assert data["workflowId"] == "campus-maintenance-alert"


def test_auto_generated_transaction_id():
    payload = {
        "workflowId": "campus-bulletin",
        "to": "student-10492",
        "payload": {"notice": "Library floor 2 reserved"},
    }
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True
    assert data["transactionId"].startswith("tx-")
    assert data["workflowId"] == "campus-bulletin"


def test_recipient_array_accepted():
    payload = {
        "workflowId": "campus-maintenance-alert",
        "to": ["student-101", "student-102", "student-103"],
        "payload": {"title": "Power testing"},
        "transactionId": "tx-multi-recipients-01",
    }
    response = client.post(
        "/v1/events/trigger",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True


def test_idempotency_duplicate_transaction_id_returns_duplicate_ignored():
    tx_id = "tx-idempotent-test-01"
    payload = {
        "workflowId": "campus-maintenance-alert",
        "to": "student-10492",
        "payload": {"title": "Maintenance Test"},
        "transactionId": tx_id,
    }
    # Initial trigger
    res1 = client.post("/v1/events/trigger", headers=AUTH_HEADERS, json=payload)
    assert res1.status_code == 202
    assert res1.json()["status"] == "processed"

    # Duplicate trigger with same transactionId
    res2 = client.post("/v1/events/trigger", headers=AUTH_HEADERS, json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["acknowledged"] is True
    assert data2["status"] == "duplicate_ignored"
    assert data2["transactionId"] == tx_id


def test_idempotency_key_header():
    idempotency_key = "idemp-header-key-999"
    payload = {
        "workflowId": "campus-bulletin",
        "to": "student-10492",
        "payload": {"notice": "Idempotency Header Notice"},
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": idempotency_key}

    # Initial trigger
    res1 = client.post("/v1/events/trigger", headers=headers, json=payload)
    assert res1.status_code == 202

    # Duplicate trigger with same Idempotency-Key header
    res2 = client.post("/v1/events/trigger", headers=headers, json=payload)
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate_ignored"


# ---------------------------------------------------------
# Bulk Trigger Tests
# ---------------------------------------------------------

def test_bulk_trigger_success():
    payload = {
        "events": [
            {
                "workflowId": "campus-bulletin",
                "to": "student-10492",
                "payload": {"notice": "Library Floor 3 closed for study group", "category": "Facilities"},
                "transactionId": "tx-bulk-1",
            },
            {
                "workflowId": "campus-bulletin",
                "to": "student-10492",
                "payload": {"notice": "Campus shuttle route B delayed 10 mins", "category": "Transit"},
                "transactionId": "tx-bulk-2",
            },
        ]
    }
    response = client.post(
        "/v1/events/trigger-bulk",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["acknowledged"] is True
    assert data["count"] == 2
    assert data["transactionIds"] == ["tx-bulk-1", "tx-bulk-2"]


def test_bulk_trigger_invalid_workflow_fails():
    payload = {
        "events": [
            {
                "workflowId": "non-existent-wf",
                "to": "student-10492",
                "payload": {"notice": "Test"},
            }
        ]
    }
    response = client.post(
        "/v1/events/trigger-bulk",
        headers=AUTH_HEADERS,
        json=payload,
    )
    assert response.status_code == 404
