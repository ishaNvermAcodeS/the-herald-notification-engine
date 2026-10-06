# OBSERVATIONS.md

# Reverse-Engineering Observations — Novu Notification Engine

## 1. Purpose

This document records factual observations from the reverse-engineering of the original Novu repository.

The analysis focuses only on the notification-engine capabilities relevant to our challenge:

- Event ingestion
- Workflow execution
- Notification job creation
- Digest/grouping
- Subscriber channel preferences
- Retry behavior
- Idempotency and duplicate prevention
- In-app and email delivery
- Relevant persistence models

All important technical observations are backed by source files and line ranges from the original repository.

The observations in this document describe how the original system works. They are not instructions to copy the original implementation.

---

# 2. High-Level Architecture

Novu is organized as a monorepo using Nx and pnpm. The relevant notification flow is distributed across API applications, worker processes, shared application logic, and the data-access layer.

## 2.1 API Layer

### `apps/api`

The API layer handles:

- Event ingestion
- Subscriber operations
- Workflow/template operations
- Subscriber preference updates
- In-app notification retrieval

Relevant controllers include:

- `apps/api/src/app/events/events.controller.ts:61-135`
- `apps/api/src/app/subscribers/subscribersV1.controller.ts:61-120`

The event controller exposes the main event-triggering endpoint.

---

## 2.2 Worker Layer

### `apps/worker`

The worker application performs asynchronous notification processing.

Relevant workers include:

- `workflow.worker.ts:24-67`
- `subscriber-process.worker.ts:33-79`
- `standard.worker.ts:35-98`

These workers are responsible for processing workflow events, subscriber jobs, delayed/digest jobs, and delivery jobs.

---

## 2.3 Application Logic

### `libs/application-generic`

This layer contains shared application/domain logic including:

- Event triggering
- Multicast/fan-out
- Notification job creation
- Subscriber preference evaluation
- Condition/filter evaluation
- Queue abstractions

Relevant use cases include:

- `TriggerEvent`
- `TriggerMulticast`
- `CreateNotificationJobs`
- `GetSubscriberTemplatePreference`
- `ConditionsFilter`

---

## 2.4 Data Access Layer

### `libs/dal`

The data-access layer uses MongoDB through Mongoose.

Relevant repositories include:

- `NotificationRepository`
- `JobRepository`
- `MessageRepository`
- `PreferencesRepository`
- `SubscriberRepository`

These repositories provide persistence for notification records, workflow jobs, delivered messages, preferences, and subscribers.

---

# 3. Event Ingestion

## 3.1 Trigger API

**Source:**  
`apps/api/src/app/events/events.controller.ts:88-135`

**Endpoint:**

`POST /v1/events/trigger`

The client can provide information including:

- `name`
- `to`
- `payload`
- `overrides`
- `transactionId`

Before processing the event, the controller checks the organization's kill switch.

The request is then converted into a `ParseEventRequestMulticastCommand` and passed to the event parsing use case.

---

## 3.2 Event Parsing and Queue Dispatch

**Source:**  
`apps/api/src/app/events/usecases/parse-event-request/parse-event-request.usecase.ts:92-127`

**Queue dispatch:**  
`parse-event-request.usecase.ts:413-427`

The event parser:

1. Uses the supplied `transactionId`, or generates one if absent.
2. Validates the recipient.
3. Validates the event payload against the workflow schema.
4. Builds the workflow job payload.
5. Places the workflow job onto the workflow queue.

The queue job is associated with the organization through `groupId`.

The API then returns an acknowledgement containing the transaction ID.

### Observation

Novu separates **event ingestion** from **notification execution**. The API accepts and validates the event, while asynchronous workers perform the actual notification processing.

---

# 4. Workflow Processing

## 4.1 Workflow Worker

**Sources:**

- `apps/worker/src/app/workflow/services/workflow.worker.ts:83-120`
- `libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts:59-106`

The workflow worker consumes events from the workflow queue and invokes `TriggerEvent`.

Before continuing, Novu validates the transaction ID.

**Source:**  
`trigger-event.usecase.ts:390-404`

If an existing job with the same transaction ID already exists for the environment, the event is rejected.

### Observation

Transaction IDs provide one layer of trigger-level duplicate protection.

---

# 5. Subscriber Fan-Out

## 5.1 Multicast Processing

**Sources:**

- `trigger-multicast.usecase.ts:47-60`
- `trigger-base.usecase.ts:57-71, 88-113`

After the event is triggered, `TriggerMulticast` separates recipients and prepares subscriber-processing jobs.

The recipients are transformed into subscriber job objects and placed into the subscriber-process queue.

### Observation

The notification pipeline separates:

`Event → Workflow processing → Recipient processing`

This allows one incoming event to fan out to multiple subscribers asynchronously.

---

# 6. Subscriber Processing and Notification Jobs

## 6.1 Subscriber Job Processing

**Sources:**

- `subscriber-process.worker.ts:91-131`
- `subscriber-job-bound.usecase.ts:81-257`

The subscriber worker executes `SubscriberJobBound`.

This process:

1. Ensures that the subscriber exists.
2. Creates or updates subscriber information when necessary.
3. Calls `CreateNotificationJobs`.
4. Persists the generated jobs.
5. Starts workflow execution.

---

## 6.2 Notification Job Creation

**Source:**  
`create-notification-jobs.usecase.ts:57-114, 117-138`

Novu creates a parent `Notification` record and generates individual jobs for the active workflow steps.

For example, a workflow may contain:

`Digest → Email → In-App`

Each workflow step becomes a separate notification job.

Jobs initially use a pending state.

Digest-specific metadata is also generated, including:

- `digestKey`
- `digestValue`

---

## 6.3 Workflow Job Chaining

**Sources:**

- `store-subscriber-jobs.usecase.ts:26-54`
- `job.repository.ts:49-64`

Workflow jobs are linked together using `_parentId`.

The next job references the previous job, creating a sequential workflow chain.

### Observation

This means notification execution is not treated as one monolithic operation. Each workflow step is represented as an individual job whose execution can determine whether the next step proceeds.

---

# 7. Killer Test #1 — Digest

## Requirement

> Ten events arriving within five minutes should become a single digest.

## 7.1 Digest Creation

**Sources:**

- `add-job.usecase.ts:188-192, 274-301, 364-391, 1095-1141`
- `merge-or-create-digest.usecase.ts:38-62, 82-124, 161-177`
- `digest.usecase.ts:48-118`

When a digest job is encountered, Novu checks whether an existing delayed digest job with the same digest value already exists.

**Source:**  
`merge-or-create-digest.usecase.ts:161-177`

If no existing digest is found:

1. The current job becomes the digest master.
2. The job is marked as the active digest.
3. A delayed queue job is created.

For a five-minute digest window, the delay is approximately:

`300,000 ms`

The delayed job is placed on the standard queue.

---

## 7.2 Subsequent Events

When additional events arrive during the digest window, Novu checks for an existing delayed digest with the same digest value.

If one exists:

1. The new job is marked as merged.
2. `_mergedDigestId` points to the existing digest master.
3. The notification references the active digest notification.
4. Execution of the merged job stops.

**Sources:**

- `merge-or-create-digest.usecase.ts:82-124`
- `add-job.usecase.ts:284-298`

### Observation

Instead of creating a separate downstream notification for every event, Novu attaches subsequent events to the active digest window.

---

## 7.3 Digest Window Completion

When the master digest's delay expires, the job is processed by the standard worker.

**Sources:**

- `run-job.usecase.ts:93-140`
- `digest.usecase.ts:48-118`

The digest logic queries merged jobs associated with the master digest.

The relevant query uses:

- `_mergedDigestId`
- subscriber ID
- environment ID
- `MERGED` status
- digest job type

**Source:**  
`digest.usecase.ts:105-118`

The resulting events are assembled into the digest payload.

The next workflow step is then queued.

### Result

For ten events belonging to the same digest window:

`Event 1`
→ becomes digest master

`Events 2–10`
→ merge into Event 1's digest

After the digest window expires:

`1 digest`
→ contains all 10 events

### Key Observation

Novu implements digesting using a **delayed master job + merged child jobs** model rather than immediately sending every event independently.

---

# 8. Killer Test #2 — Channel Preferences

## Requirement

> If a user has muted email, they should receive the notification through in-app only.

## 8.1 Preference Storage

**Sources:**

- `preferences.schema.ts:36-80`
- `preferences.schema.ts:48-80`

Subscriber preferences contain channel-specific settings.

For example:

```text
email:
    enabled: false

in_app:
    enabled: true
