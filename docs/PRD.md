# Product Requirements Document (PRD): Clean-Room Notification Engine

## 1. Problem Statement
Campus communication systems suffer from three critical breakdowns:
1. **Notification Storms & Fatigue**: High-frequency campus announcements (weather advisories, parking changes, shuttle disruptions, classroom moves) blast students individually, causing inbox clutter, notification blindness, and missed critical updates.
2. **Preference Violations**: Students who mute noisy delivery channels (e.g., email) often continue to receive messages due to crude all-or-nothing broadcast configurations or bypassed channel-level controls.
3. **Delivery Loss & Duplicate Spam**: Network glitches or email provider downtime either permanently drop critical notifications or, upon uncoordinated retry, flood students with duplicate messages.

Our goal is to build a high-performance, resilient, clean-room notification engine designed specifically for campus alerts that reliably batches event bursts, strictly enforces recipient preferences, retries transient failures idempotently, and provides intelligent synthesis.

---

## 2. Target Users & Personas

1. **Campus Dispatcher / Administrator**:
   - Triggers event notifications for emergencies, campus services, course updates, and maintenance advisories via REST APIs or dashboard triggers.
   - Requires guaranteed delivery, real-time observability, and auditability.

2. **Student / Faculty Subscriber**:
   - End-recipient of campus communications.
   - Configures granular channel preferences (e.g., mute email while keeping in-app alerts active).
   - Receives consolidated, actionable digests instead of disjointed notification floods.

3. **System Integrator / Automation Service**:
   - Third-party university systems (e.g., Canvas, Registrar, Campus Police CAD, Facilities IoT) that fire high-volume webhook triggers into the notification engine.

---

## 3. Core Functionality & Scope Boundary

The rebuild focuses strictly on the core engine required for the challenge, omitting extraneous enterprise features (e.g., multi-tenancy billing, SAML SSO, translation suites, custom MCP agents):

1. **Event Ingestion**:
   - Authenticated HTTP API accepting trigger requests with payloads, recipient identifiers, and transaction tokens.
   - Immediate asynchronous acknowledgment with idempotent deduplication.

2. **Workflow & Step Execution Engine**:
   - Step sequence orchestration supporting sequential pipelines: Trigger -> Digest -> Channel Dispatch (Email / In-App).
   - State machine tracking step lifecycle (`PENDING` -> `QUEUED` -> `RUNNING` -> `COMPLETED` / `FAILED` / `SKIPPED` / `MERGED`).

3. **Intelligent Digest & Grouping**:
   - Time-windowed aggregation of related events for a given subscriber and workflow.
   - Master digest timer scheduling and child event merging.

4. **Recipient Channel Preferences**:
   - Per-subscriber channel enablement settings (`email`, `in_app`).
   - Dynamic step evaluation: non-critical steps on muted channels are cleanly skipped while unmuted channels proceed.

5. **Fault-Tolerant Delivery & Idempotency**:
   - Exponential backoff retry loop for transient delivery errors.
   - Idempotent message upsert and deduplicated provider dispatch preventing duplicate notifications.

6. **In-App Message Feed**:
   - Persistent subscriber inbox storing delivered notifications with read/unread tracking.

---

## 4. Functional Requirements

### 4.1 Event Ingestion & Triggering
- **FR-1.1**: The system must provide a `POST /v1/events/trigger` endpoint accepting:
  - `workflowId`: Identifier of the workflow template.
  - `to`: Target subscriber identifier or array of subscriber identifiers.
  - `payload`: Arbitrary JSON key-value data for template compilation.
  - `transactionId` (optional): Unique client transaction identifier for idempotency.
- **FR-1.2**: If a request includes a `transactionId` that has already been ingested within the retention window, the system must recognize it as duplicate and return the previous transaction acknowledgement rather than re-executing or failing.
- **FR-1.3**: The ingestion endpoint must validate inputs and acknowledge receipt within 100ms, dispatching workflow jobs to background processing queues asynchronously.

### 4.2 Digest & Event Aggregation
- **FR-2.1**: A workflow may define a `DIGEST` step with a configurable time window (e.g., 5 minutes) and optional `digestKey` grouping property.
- **FR-2.2**: The first event arriving in an idle digest window must be designated the **Digest Master** and schedule a delayed job corresponding to the window duration.
- **FR-2.3**: Any subsequent events arriving for the same subscriber and workflow while the digest window is active must be designated **Merged Events**; their individual downstream channel steps must be halted.
- **FR-2.4**: When the digest timer expires, the master job must aggregate all merged event payloads into an array `digest.events` and pass them to downstream channel steps (Email, In-App).

### 4.3 Channel Selection & Preference Enforcement
- **FR-3.1**: The system must store per-subscriber channel preferences:
  - Global channel preferences (`channels.email`, `channels.in_app`).
  - Per-workflow channel preferences overriding global settings.
- **FR-3.2**: When executing a message step (e.g., `EMAIL`), the engine must evaluate the subscriber's preference for that channel.
- **FR-3.3**: If the channel is disabled/muted by the subscriber:
  - The step execution must be marked `SKIPPED` with reason `SUBSCRIBER_PREFERENCE`.
  - The provider send must NOT be invoked.
  - Downstream steps on other channels (e.g., `IN_APP`) must continue execution unaffected.
- **FR-3.4**: Critical / emergency workflows must have the ability to override channel mutes when explicitly flagged by authorized campus dispatchers.

### 4.4 Resilient Delivery, Retries & Idempotency
- **FR-4.1**: When a channel provider send fails due to transient faults (network timeout, rate limit 429, 5xx gateway error), the job must not be permanently failed on the first attempt.
- **FR-4.2**: The engine must automatically retry failed sends with exponential backoff (e.g., attempt 1: 1s, attempt 2: 5s, attempt 3: 15s) up to a configurable maximum attempt count.
- **FR-4.3**: Each message send attempt must use deterministic message identity (`_jobId` / deterministic key). Re-execution of a step must update the existing message record rather than creating a duplicate notification in the database or in the student's feed.
- **FR-4.4**: If the provider send ultimately succeeds on attempt 2 or 3, the final message state must reflect `SENT` / `DELIVERED`, and exactly one notification must exist.

### 4.5 In-App Notification Feed
- **FR-5.1**: Deliveries to the `IN_APP` channel must create entries in the subscriber's message inbox.
- **FR-5.2**: The system must provide `GET /v1/subscribers/:subscriberId/notifications/feed` returning paginated in-app notifications with read/seen status.
- **FR-5.3**: The system must provide `PATCH /v1/subscribers/:subscriberId/messages/:messageId/read` to mark notifications as read.

---

## 5. Acceptance Criteria: The 3 Killer Tests

| Killer Test | Objective | Acceptance Criteria |
|---|---|---|
| **Killer Test #1: Digest Grouping** | Ten events arriving within five minutes must become a single digest. | 1. Trigger 10 distinct events for subscriber `student-001` within a 5-minute window on a digested workflow.<br>2. Exactly 1 digest master job is created; 9 events are recorded as `MERGED`.<br>3. After the 5-minute timer expires, exactly 1 consolidated notification is delivered to the subscriber.<br>4. The delivered notification contains all 10 events in `digest.events`. |
| **Killer Test #2: Preference Filtering** | A user who has muted email must receive in-app notifications only. | 1. Configure subscriber `student-002` with `preferences.channels.email = false` and `preferences.channels.in_app = true`.<br>2. Trigger a multi-channel workflow containing both Email and In-App steps.<br>3. The Email step is recorded as `SKIPPED` with reason `SUBSCRIBER_PREFERENCE`; zero emails are dispatched via provider.<br>4. The In-App step executes successfully; the notification appears in `student-002`'s in-app feed. |
| **Killer Test #3: Retry Without Duplicates** | A failed send must be retried without creating duplicate notifications. | 1. Configure or simulate an email provider failure on the initial send attempt.<br>2. The engine catches the failure, schedules an exponential backoff retry, and re-executes the send.<br>3. Upon retry success, exactly 1 `Message` record exists for the step.<br>4. The subscriber receives exactly 1 email notification; no duplicate emails or phantom feed items are created. |

---

## 6. The Clean-Room Fix Feature

### Feature Name:
**Atomic Digest Window Mutex (Distributed Lock for Digest Master Election)**

### Problem in Original Code:
In Novu's `MergeOrCreateDigest.execute` ([merge-or-create-digest.usecase.ts:161-177](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L161-L177)), the check for an existing delayed digest job is decoupled from the write that marks the job as master (`getExistingDelayedJobWithTheSameDigestValue` followed by `markJobAsDigestMaster`). Under concurrent event arrival (e.g., two events arriving within the same 50ms window across distributed workers), both queries return `null`. Both events claim master status, spawning TWO parallel digest timers and splitting the batch into duplicate digests.

### Rebuild Fix Requirement:
- The clean-room engine must implement an atomic mutex / distributed lease (via Redis `SET NX EX` or atomic MongoDB find-and-modify on a dedicated `digest_windows` collection).
- Only the transaction that atomically acquires the window lease may initialize the digest timer; all concurrent contenders are guaranteed to merge into the acquired active window ID, eliminating split-brain digest duplication.

---

## 7. The Differentiator Feature

### Feature Name:
**AI-Powered Smart Digest Synthesis (Campus Notification Brief)**

### Problem Solved:
Standard digest engines (including Novu) simply concatenate raw event payloads into a chronological JSON list. When 10 distinct campus events occur in 5 minutes (e.g., bus reroutes, dorm power maintenance, exam room change, dining hall closure, weather notice), students are presented with an overwhelming, disjointed list of separate items.

### Rebuild Differentiator Requirement:
- When a digest window completes, the aggregated event array is passed to an AI Synthesis module before rendering.
- The module generates:
  1. **Executive TL;DR**: A 2-sentence overarching campus status summary.
  2. **Urgent Action Items**: High-priority tasks requiring student attention (e.g., "Move vehicle from Lot B before 4 PM").
  3. **Categorized Notices**: Grouped bulletins (Academic, Housing, Transportation).
- The synthesized brief is embedded directly into the delivered digest notification, transforming 10 raw alerts into a cohesive, high-impact campus briefing.
