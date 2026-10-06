# Clean-Room Data Model Specification

## 1. Overview & Data Modeling Principles
This document defines the minimal, clean-room data model required to power our campus notification engine. Rather than replicating the 40+ database collections found in Novu, this model derives only the necessary entities strictly justified by:
- Event ingestion and workflow execution
- **Killer Test 1**: 10 events within 5 minutes digested into 1 notification
- **Killer Test 2**: Email muted -> In-App only preference enforcement
- **Killer Test 3**: Failed send retried without creating duplicate notifications
- **Final Fix**: Atomic digest window state tracking
- **Final Differentiator**: AI-synthesized digest brief storage

---

## 2. Entity-Relationship Diagram

```
+--------------------+            +------------------------+
|     Subscriber     | 1        * |       Preference       |
|--------------------|<-----------|------------------------|
| id (PK)            |            | id (PK)                |
| subscriberId (UQ)  |            | subscriberId (FK)      |
| email              |            | workflowId (FK, opt)   |
| name               |            | channels (JSON)        |
+---------+----------+            +------------------------+
          | 1
          |
          | *
+---------v----------+            +------------------------+
|    Notification    | 1        * |          Job           |
|--------------------|<-----------|------------------------|
| id (PK)            |            | id (PK)                |
| transactionId (UQ) |            | notificationId (FK)    |
| subscriberId (FK)  |            | parentJobId (FK, opt)  |
| workflowId (FK)    |            | stepType (ENUM)        |
| payload (JSON)     |            | status (ENUM)          |
| digestWindowId(FK) |            | attempts (INT)         |
+--------------------+            +-----------+------------+
                                              | 1
                                              |
                                              | 0..1
                                  +-----------v------------+
                                  |        Message         |
                                  |------------------------|
                                  | id (PK)                |
                                  | jobId (FK)             |
                                  | subscriberId (FK)      |
                                  | channel (ENUM)         |
                                  | status (ENUM)          |
                                  | deduplicationKey (UQ)  |
                                  | read / seen (BOOL)     |
                                  +------------------------+
```

---

## 3. Entity Specifications

### 3.1 `Subscriber`
- **Purpose**: Represents the recipient of campus notifications (student, faculty, staff).
- **Justification**: Needed to resolve recipient delivery addresses (email), associate preferences, and anchor the in-app message feed.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Internal unique identifier |
| `subscriberId` | String | Unique Index, Not Null | External unique identifier (e.g., student ID `student-10492`) |
| `email` | String | Index, Nullable | Recipient email address for email channel dispatch |
| `name` | String | Nullable | Full display name of the subscriber |
| `createdAt` | Timestamp | Not Null | Record creation timestamp |
| `updatedAt` | Timestamp | Not Null | Record update timestamp |

---

### 3.2 `Preference`
- **Purpose**: Governs channel permissions per subscriber.
- **Justification**: **Directly drives Killer Test 2**. Evaluated before dispatching any message step to verify if the channel is enabled or muted.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Internal identifier |
| `subscriberId` | String | FK, Index, Not Null | References `Subscriber.subscriberId` |
| `workflowId` | String | FK, Index, Nullable | If set, applies specifically to this workflow; if null, applies globally |
| `channels` | JSON Object | Not Null | Channel toggle map: `{ "email": boolean, "in_app": boolean }` |
| `createdAt` | Timestamp | Not Null | Timestamp of preference creation |
| `updatedAt` | Timestamp | Not Null | Timestamp of preference update |

- **Indexes**:
  - Unique compound index on `(subscriberId, workflowId)` (where null workflowId represents the global preference).

---

### 3.3 `Workflow`
- **Purpose**: Blueprint defining notification orchestration steps and metadata.
- **Justification**: Defines whether a workflow includes a digest window, which channels to invoke, and default urgency.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | String | PK, Not Null | Workflow identifier (e.g., `campus-maintenance-alert`) |
| `name` | String | Not Null | Human-readable name |
| `description` | String | Nullable | Overview of workflow purpose |
| `isCritical` | Boolean | Default: false | If true, overrides subscriber channel mute for emergency safety alerts |
| `steps` | JSON Array | Not Null | Ordered array of step definitions (e.g., `[{ type: "DIGEST", durationMs: 300000 }, { type: "EMAIL" }, { type: "IN_APP" }]`) |
| `createdAt` | Timestamp | Not Null | Creation timestamp |

---

### 3.4 `DigestWindow` (Final Fix Support)
- **Purpose**: Manages active, atomic digest grouping state per subscriber and workflow.
- **Justification**: **Directly drives Killer Test 1 and the Final Fix**. Resolves the race condition where concurrent events create duplicate digest timers.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Window unique identifier |
| `subscriberId` | String | Index, Not Null | Target subscriber |
| `workflowId` | String | Index, Not Null | Target workflow identifier |
| `digestKey` | String | Nullable | Optional partition key (e.g., event category or location) |
| `status` | Enum | Not Null | `OPEN`, `EXPIRED`, `DISPATCHED` |
| `masterJobId` | String | Not Null | The Job ID responsible for the delayed digest execution |
| `windowEndsAt` | Timestamp | Index, Not Null | Exact timestamp when the digest window closes |
| `eventCount` | Integer | Default: 1 | Number of events merged into this active window |
| `createdAt` | Timestamp | Not Null | Window start time |

- **Unique Constraint**:
  - Partial unique index on `(subscriberId, workflowId, digestKey)` WHERE `status = 'OPEN'`.
  - Ensures **strictly one open window** exists for a given subscriber and workflow at any moment.

---

### 3.5 `Notification`
- **Purpose**: Records each distinct incoming event trigger instance.
- **Justification**: Stores the event payload, client transaction ID, and links to the parent digest window if merged.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Notification unique identifier |
| `transactionId` | String | Unique Index, Not Null | Client-supplied or generated idempotency transaction key |
| `subscriberId` | String | Index, Not Null | References target subscriber |
| `workflowId` | String | Index, Not Null | Workflow triggered |
| `payload` | JSON Object | Not Null | Raw event data submitted with trigger |
| `status` | Enum | Not Null | `PENDING`, `PROCESSING`, `MERGED`, `COMPLETED`, `FAILED` |
| `digestWindowId`| UUID / String | FK, Index, Nullable | Points to `DigestWindow.id` if digested |
| `createdAt` | Timestamp | Not Null | Ingestion timestamp |

---

### 3.6 `Job`
- **Purpose**: The atomic execution unit for each workflow step.
- **Justification**: Manages lifecycle, step dependencies, delayed scheduling, and retry attempts.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Job identifier |
| `notificationId`| UUID / String | FK, Index, Not Null | Parent notification reference |
| `parentJobId` | UUID / String | FK, Index, Nullable | Predecessor step in pipeline (enables step chaining) |
| `subscriberId` | String | Index, Not Null | Target subscriber |
| `stepType` | Enum | Not Null | `DIGEST`, `EMAIL`, `IN_APP` |
| `status` | Enum | Index, Not Null | `PENDING`, `DELAYED`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`, `MERGED` |
| `skipReason` | Enum | Nullable | `SUBSCRIBER_PREFERENCE`, `CONDITIONS_UNMET` |
| `attempts` | Integer | Default: 0 | Number of execution attempts made |
| `maxAttempts` | Integer | Default: 3 | Maximum allowed retry attempts |
| `delayUntil` | Timestamp | Index, Nullable | Scheduled release time for delayed digest jobs |
| `error` | String | Nullable | Error message if step execution failed |
| `digestMetadata`| JSON Object | Nullable | Stores aggregated events array and AI summary when digest completes |
| `createdAt` | Timestamp | Not Null | Job creation timestamp |
| `updatedAt` | Timestamp | Not Null | Job update timestamp |

---

### 3.7 `Message`
- **Purpose**: Represents the actual communication artifact delivered to a channel.
- **Justification**: **Directly drives Killer Test 3 and In-App feeds**. Holds message body, delivery status, and idempotency keys to prevent duplicate sends on retry.

| Field | Type | Modifiers | Description |
|---|---|---|---|
| `id` | UUID / String | PK, Not Null | Message identifier |
| `jobId` | UUID / String | FK, Index, Not Null | Step Job that produced this message |
| `subscriberId` | String | Index, Not Null | Target recipient |
| `channel` | Enum | Not Null | `EMAIL`, `IN_APP` |
| `status` | Enum | Index, Not Null | `PENDING`, `SENT`, `FAILED` |
| `subject` | String | Nullable | Message subject line |
| `content` | String / Text | Not Null | Message content (HTML or plain text) |
| `aiSummary` | JSON Object | Nullable | **Differentiator**: Structured AI briefing for digested notifications |
| `deduplicationKey`| String | Unique Index, Not Null | Deterministic hash `(subscriberId, workflowId, stepType, transactionId)` |
| `providerMessageId`| String | Nullable | External ID returned by email provider (e.g. SMTP/SendGrid ID) |
| `read` | Boolean | Default: false | Read status for in-app inbox |
| `seen` | Boolean | Default: false | Seen status for in-app badge count |
| `createdAt` | Timestamp | Index, Not Null | Dispatch timestamp |
| `deliveredAt` | Timestamp | Nullable | Confirmation timestamp of provider delivery |

---

## 4. State Transitions

### Job State Transition Model
```
[PENDING] ---> [DELAYED] (Digest Master timer active)
   |                |
   |                v
   +---------> [RUNNING] (Worker claimed execution)
                 |   |
   +-------------+   +--------------+
   |                                |
   v                                v
[COMPLETED]                    [FAILED] (attempts < max: RETRYING -> RUNNING)
   |
   +---> (If channel preference disabled: [SKIPPED])
   +---> (If absorbed into active digest: [MERGED])
```

### Digest Window State Transition Model
```
[OPEN] (Master created, accepting merged events 2-10)
   |
   v  (Timer expires at t + 5m)
[EXPIRED] (Aggregation & AI synthesis active)
   |
   v
[DISPATCHED] (Handed off to downstream Email/In-App jobs)
```
