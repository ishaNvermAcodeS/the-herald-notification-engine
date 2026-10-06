# Clean-Room API Specification

## 1. Overview & Conventions
This API specification defines the external and internal HTTP interfaces for our clean-room Campus Notification Engine.
- **Base Path**: `/v1`
- **Format**: JSON (`Content-Type: application/json`)
- **Authentication**: Bearer API token via `Authorization: Bearer <API_KEY>` or x-api-key header.
- **Idempotency**: Supported on trigger mutations via `Idempotency-Key` header or request body `transactionId`.

---

## 2. Event Ingestion & Trigger APIs

### 2.1 Trigger Single Event
Triggers an event workflow for one or more subscribers.

- **Method**: `POST`
- **Path**: `/v1/events/trigger`
- **Headers**:
  - `Idempotency-Key` (Optional, String): Client idempotency key (cached for 24 hours).
- **Request Body**:
```json
{
  "workflowId": "campus-maintenance-alert",
  "to": "student-10492",
  "payload": {
    "title": "Water Outage in West Dorm",
    "location": "West Dorm",
    "severity": "medium",
    "details": "Water pressure will be reduced between 2 PM and 4 PM."
  },
  "transactionId": "tx-campus-alert-20261005-01"
}
```

- **Validation Rules**:
  - `workflowId`: Required, string, must match a registered workflow.
  - `to`: Required, string or array of strings (max 100 per call).
  - `payload`: Required, JSON object.
  - `transactionId`: Optional, string, max 128 characters.

- **Success Response** (`202 Accepted`):
```json
{
  "acknowledged": true,
  "status": "processed",
  "transactionId": "tx-campus-alert-20261005-01",
  "workflowId": "campus-maintenance-alert"
}
```

- **Idempotency Behavior**:
  - If a request with the same `transactionId` or `Idempotency-Key` arrives:
    - If the previous trigger is still in flight: Returns `202 Accepted` with status `"in-progress"`.
    - If the previous trigger has completed: Returns `200 OK` with the existing transaction status and status `"duplicate_ignored"`.
    - **Never throws an unhandled 400 error** on duplicate trigger.

- **Error Responses**:
  - `400 Bad Request`: Missing `workflowId` or invalid payload format.
  - `404 Not Found`: `workflowId` does not exist.

---

### 2.2 Bulk Trigger Events (Testing & Load Helper)
Ingests multiple event triggers in a single atomic batch (ideal for demonstrating Killer Test 1: 10 events within 5 minutes).

- **Method**: `POST`
- **Path**: `/v1/events/trigger-bulk`
- **Request Body**:
```json
{
  "events": [
    {
      "workflowId": "campus-bulletin",
      "to": "student-10492",
      "payload": { "notice": "Library Floor 3 closed for study group", "category": "Facilities" }
    },
    {
      "workflowId": "campus-bulletin",
      "to": "student-10492",
      "payload": { "notice": "Campus shuttle route B delayed 10 mins", "category": "Transit" }
    }
  ]
}
```

- **Success Response** (`202 Accepted`):
```json
{
  "acknowledged": true,
  "count": 2,
  "transactionIds": ["tx-bulk-1", "tx-bulk-2"]
}
```

---

## 3. Subscriber Preferences APIs (Killer Test 2)

### 3.1 Get Subscriber Preferences
Retrieves the active channel delivery preferences for a subscriber.

- **Method**: `GET`
- **Path**: `/v1/subscribers/:subscriberId/preferences`
- **Path Parameters**:
  - `subscriberId` (Required, String): Target student ID.

- **Success Response** (`200 OK`):
```json
{
  "subscriberId": "student-10492",
  "global": {
    "email": true,
    "in_app": true
  },
  "workflows": {
    "campus-bulletin": {
      "email": false,
      "in_app": true
    }
  }
}
```

---

### 3.2 Update Subscriber Preferences
Updates global or workflow-specific channel preferences (e.g., mute email for campus bulletins).

- **Method**: `PATCH`
- **Path**: `/v1/subscribers/:subscriberId/preferences`
- **Request Body**:
```json
{
  "workflowId": "campus-bulletin",
  "channels": {
    "email": false,
    "in_app": true
  }
}
```

- **Validation Rules**:
  - `workflowId`: Optional string. If omitted, updates global preferences.
  - `channels`: Required object with at least one channel boolean (`email`, `in_app`).

- **Success Response** (`200 OK`):
```json
{
  "subscriberId": "student-10492",
  "updated": {
    "workflowId": "campus-bulletin",
    "channels": {
      "email": false,
      "in_app": true
    }
  }
}
```

---

## 4. In-App Notifications Feed APIs

### 4.1 Get In-App Notification Feed
Fetches the in-app message inbox for a student.

- **Method**: `GET`
- **Path**: `/v1/subscribers/:subscriberId/notifications/feed`
- **Query Parameters**:
  - `page` (Optional, Integer, Default: 1): Page number.
  - `limit` (Optional, Integer, Default: 20): Items per page.
  - `read` (Optional, Boolean): Filter by read status.

- **Success Response** (`200 OK`):
```json
{
  "data": [
    {
      "id": "msg-9921",
      "subject": "Campus Digest: 10 Updates Available",
      "content": "Here is your consolidated campus update for the last 5 minutes.",
      "channel": "IN_APP",
      "read": false,
      "seen": true,
      "createdAt": "2026-10-05T21:40:00Z",
      "aiSummary": {
        "tldr": "Campus services are operating normally with minor shuttle delays and temporary 3rd-floor library maintenance.",
        "urgentActions": [
          "Move vehicles from Lot B before 4 PM today."
        ],
        "categories": {
          "Transit": ["Shuttle B delayed 10m"],
          "Facilities": ["Library floor 3 closed", "West dorm water reduced 2-4 PM"]
        }
      }
    }
  ],
  "total": 1,
  "unreadCount": 1
}
```

---

### 4.2 Mark In-App Notification as Read
Marks an individual in-app notification message as read.

- **Method**: `PATCH`
- **Path**: `/v1/subscribers/:subscriberId/messages/:messageId/read`
- **Request Body**:
```json
{
  "read": true
}
```

- **Success Response** (`200 OK`):
```json
{
  "id": "msg-9921",
  "read": true,
  "updatedAt": "2026-10-05T21:45:00Z"
}
```

---

## 5. Inspection & Observability APIs

### 5.1 Inspect Notification Execution Trace
Allows inspecting the exact execution status, digest grouping, step outcomes, and retry counts for a given transaction.

- **Method**: `GET`
- **Path**: `/v1/notifications/:transactionId/status`

- **Success Response** (`200 OK`):
```json
{
  "transactionId": "tx-campus-alert-20261005-01",
  "workflowId": "campus-bulletin",
  "subscriberId": "student-10492",
  "status": "COMPLETED",
  "isDigest": true,
  "digestWindow": {
    "status": "DISPATCHED",
    "eventCount": 10,
    "windowDurationSeconds": 300
  },
  "steps": [
    {
      "stepType": "DIGEST",
      "status": "COMPLETED"
    },
    {
      "stepType": "EMAIL",
      "status": "SKIPPED",
      "skipReason": "SUBSCRIBER_PREFERENCE"
    },
    {
      "stepType": "IN_APP",
      "status": "COMPLETED",
      "attempts": 1,
      "messageId": "msg-9921"
    }
  ]
}
```

---

## 6. Testing & Demo Simulation Endpoints

### 6.1 Simulate Provider Failure (Killer Test 3 Demonstration)
Enables or disables simulated transient failure on the Email or In-App channel provider to demonstrate automated exponential retries without duplicate message creation.

- **Method**: `POST`
- **Path**: `/v1/testing/simulate-provider-failure`
- **Request Body**:
```json
{
  "channel": "EMAIL",
  "failTimes": 1,
  "errorType": "503_SERVICE_UNAVAILABLE"
}
```
- **Description**: The mock email provider will fail the next 1 attempt with a 503 error, then succeed on attempt 2.

- **Success Response** (`200 OK`):
```json
{
  "simulatedFailureConfigured": true,
  "channel": "EMAIL",
  "failNextAttempts": 1
}
```

---

### 6.2 Trigger Fast Digest Expiry (Testing Helper)
Forces an open digest window to expire immediately so verification tests do not need to wait the full 5 minutes in automated test suites.

- **Method**: `POST`
- **Path**: `/v1/testing/flush-digest-window`
- **Request Body**:
```json
{
  "subscriberId": "student-10492",
  "workflowId": "campus-bulletin"
}
```

- **Success Response** (`200 OK`):
```json
{
  "flushed": true,
  "digestedEventCount": 10
}
```
