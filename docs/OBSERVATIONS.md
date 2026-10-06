# Reverse-Engineering Observations: Novu Notification Engine

## Executive Overview
This document contains the factual reverse-engineering analysis of the original Novu repository (`https://github.com/novuhq/novu`). Every technical finding, execution trace, queue interaction, database schema, and edge-case behavior documented below is verified against concrete source files and exact line numbers within the repository.

---

## 1. Discovered Architecture & Relevant Components

Novu is structured as a monorepo (managed with Nx and pnpm) dividing responsibilities across modular NestJS applications, shared business logic libraries, and domain repositories:

1. **`apps/api`** (`apps/api/src/app`):
   - HTTP API surface handling event ingestion, subscriber management, template/workflow management, in-app feed retrieval, and preference mutations.
   - Core entry controller: [events.controller.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/events/events.controller.ts#L61-L135).
   - In-app and subscriber preferences controller: [subscribersV1.controller.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/subscribers/subscribersV1.controller.ts#L61-L120).

2. **`apps/worker`** (`apps/worker/src/app`):
   - Multi-queue consumer service executing async workflow steps, fan-out processing, delay/digest coordination, preference evaluations, and message delivery.
   - Core workers:
     - `WorkflowWorker` ([workflow.worker.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/workflow.worker.ts#L24-L67))
     - `SubscriberProcessWorker` ([subscriber-process.worker.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/subscriber-process.worker.ts#L33-L79))
     - `StandardWorker` ([standard.worker.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/standard.worker.ts#L35-L98))

3. **`libs/application-generic`** (`libs/application-generic/src`):
   - Shared domain services, use cases, BullMQ/SQS queue abstractions, caching wrappers, and template hydration logic.
   - Key orchestrator usecases: `TriggerEvent`, `TriggerMulticast`, `CreateNotificationJobs`, `GetSubscriberTemplatePreference`, `ConditionsFilter`.

4. **`libs/dal`** (`libs/dal/src`):
   - Data Access Layer using MongoDB via Mongoose. Repositories include:
     - `NotificationRepository` ([notification.repository.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/notification/notification.repository.ts))
     - `JobRepository` ([job.repository.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.repository.ts))
     - `MessageRepository` ([message.repository.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/message/message.repository.ts))
     - `PreferencesRepository` ([preferences.repository.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/preferences/preferences.repository.ts))
     - `SubscriberRepository` ([subscriber.repository.ts](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/subscriber/subscriber.repository.ts))

---

## 2. Event Ingestion Flow (API -> Workflow Queue)

### Step 2.1: Ingestion API Endpoint
- **Source**: [events.controller.ts:88-135](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/events/events.controller.ts#L88-L135)
- **Method & Route**: `POST /v1/events/trigger`
- **Execution**:
  - The client provides `{ name, to, payload, overrides, transactionId, ... }`.
  - The controller checks the organization kill switch (`checkKillSwitch`, lines 71-83).
  - It constructs a `ParseEventRequestMulticastCommand` and calls `ParseEventRequest.execute()`.

### Step 2.2: Parse & Dispatch to Workflow Queue
- **Source**: [parse-event-request.usecase.ts:92-127, 413-427](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/events/usecases/parse-event-request/parse-event-request.usecase.ts#L92-L127)
- **Execution**:
  - Generates or assigns `transactionId`: `command.transactionId || generateTransactionId()` (line 93).
  - Validates recipient schema (`validateRecipient`, lines 400-410).
  - Validates payload against workflow schema (`validateAndApplyPayloadDefaults`, lines 149-168).
  - Dispatches job payload `IWorkflowDataDto` to BullMQ/SQS via `WorkflowQueueService`:
    ```ts
    await this.workflowQueueService.add({
      name: transactionId,
      data: jobData,
      groupId: command.organizationId,
    });
    ```
    (lines 422-426).
  - Returns `TriggerEventResponseDto` with `{ acknowledged: true, status: 'processed', transactionId }`.

---

## 3. Workflow & Notification Job Execution Flow

### Step 3.1: Trigger Processing in WorkflowWorker
- **Source**: [workflow.worker.ts:83-120](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/workflow.worker.ts#L83-L120) & [trigger-event.usecase.ts:59-106, 177-209](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts#L59-L106)
- **Execution**:
  - `WorkflowWorker` consumes the message from the workflow queue and calls `TriggerEvent.execute(data)`.
  - Validates transaction ID uniqueness:
    ```ts
    await this.validateTransactionIdProperty(mappedCommand.transactionId, environmentId);
    ```
    ([trigger-event.usecase.ts:390-404](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts#L390-L404)). If a job with `transactionId` already exists, it throws `BadRequestException`.
  - Delegates recipient fan-out to `TriggerMulticast.execute()`.

### Step 3.2: Multicast Fan-out to Subscriber Process Queue
- **Source**: [trigger-multicast.usecase.ts:47-60](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-multicast/trigger-multicast.usecase.ts#L47-L60) & [trigger-base.usecase.ts:57-71, 88-113](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-base/trigger-base.usecase.ts#L57-L71)
- **Execution**:
  - Distinguishes single subscribers and topic subscribers (`splitByRecipientType`).
  - Calls `sendToProcessSubscriberService`, which transforms each recipient into an `IProcessSubscriberBulkJobDto` via `mapSubscribersToJobs`.
  - Enqueues chunks into `SubscriberProcessQueueService`:
    ```ts
    await this.subscriberProcessQueueService.addBulk(chunk);
    ```
    ([trigger-base.usecase.ts:61](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-base/trigger-base.usecase.ts#L61)).

### Step 3.3: Subscriber Processing & Job Construction
- **Source**: [subscriber-process.worker.ts:91-131](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/subscriber-process.worker.ts#L91-L131) & [subscriber-job-bound.usecase.ts:81-257](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/subscriber-job-bound/subscriber-job-bound.usecase.ts#L81-L257)
- **Execution**:
  - `SubscriberProcessWorker` picks up the job and executes `SubscriberJobBound.execute()`.
  - Ensures subscriber exists or creates/updates it via `CreateOrUpdateSubscriberUseCase` (lines 145-161).
  - Generates the workflow jobs by calling `CreateNotificationJobs.execute()` (line 246).
  - Persists jobs and starts workflow execution via `StoreSubscriberJobs.execute()` (lines 250-256).

### Step 3.4: Notification & Step Chaining Construction
- **Source**: [create-notification-jobs.usecase.ts:57-114, 117-138](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/create-notification-jobs/create-notification-jobs.usecase.ts#L57-L114)
- **Execution**:
  - Creates the parent `NotificationEntity` in MongoDB via `this.notificationRepository.create({...})` (lines 118-134).
  - Iterates over active workflow template steps (e.g., Digest step -> Email step -> In-App step).
  - For each step, builds a `NotificationJob` with `status: JobStatusEnum.PENDING` (lines 233-266).
  - If a digest step exists, builds metadata: `digestKey`, `digestValue` (evaluated from payload) (lines 268-301).
- **Source**: [store-subscriber-jobs.usecase.ts:26-54](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/store-subscriber-jobs/store-subscriber-jobs.usecase.ts#L26-L54) & [job.repository.ts:49-64](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.repository.ts#L49-L64)
  - `JobRepository.storeJobs` chains steps using `_parentId`:
    ```ts
    for (let index = 0; index < jobs.length; index += 1) {
      if (index > 0) {
        jobs[index]._parentId = stored[index - 1]._id;
      }
      const created = new this.MongooseModel({ ...jobs[index], createdAt: Date.now() });
      stored.push(this.mapEntity(created));
    }
    await this.insertMany(stored, true);
    ```
  - Takes `storedJobs[0]` (the first step, e.g., Trigger or Digest) and calls `AddJob.execute()`.

---

## 4. Killer Test #1: 10 Events Within 5 Minutes -> Single Digest

### Observation & Execution Trace:
- **Where**:
  - [add-job.usecase.ts:188-192, 274-301, 364-391, 1095-1141](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/add-job.usecase.ts#L188-L192)
  - [merge-or-create-digest.usecase.ts:38-62, 82-124, 161-177](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L38-L62)
  - [digest.usecase.ts:48-118](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/digest/digest.usecase.ts#L48-L118)

### Detailed Flow:
1. **Event 1 arrives**:
   - `AddJob.execute()` detects `job.type === StepTypeEnum.DIGEST` (`isJobDeferredType`).
   - Invokes `MergeOrCreateDigest.execute(command)`.
   - Calls `isMasterDigestOrShouldMergeToExisting(job, digestMeta)` ([merge-or-create-digest.usecase.ts:161-177](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L161-L177)):
     ```ts
     const delayedDigestJob = await this.jobRepository.getExistingDelayedJobWithTheSameDigestValue(job, digestMeta);
     if (!delayedDigestJob) {
       await this.jobRepository.markJobAsDigestMaster(job);
       return {
         activeDigestId: job._id,
         digestResult: DigestCreationResultEnum.CREATED,
       };
     }
     ```
   - No delayed job exists yet. Event 1 becomes the **Digest Master** (`DigestCreationResultEnum.CREATED`).
   - `AddJob` computes `delayAmount` (e.g., 5 minutes = 300,000 ms) and calls `queueJob({ job, delay, ... })` ([add-job.usecase.ts:385, 1095-1141](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/add-job.usecase.ts#L385)).
   - Enqueues to `StandardQueueService` in BullMQ with `{ delay: 300000, jobId: job._id }`.

2. **Events 2 through 10 arrive within 5 minutes**:
   - For each event, `CreateNotificationJobs` generates their respective notification and job entities.
   - When each reaches `MergeOrCreateDigest.execute()`, `getExistingDelayedJobWithTheSameDigestValue` finds Event 1's delayed digest job!
   - Returns:
     ```ts
     return {
       activeDigestId: delayedDigestJob._id,
       activeNotificationId: delayedDigestJob._notificationId?.toString(),
       digestResult: DigestCreationResultEnum.MERGED,
     };
     ```
   - `processMergedDigest()` executes ([merge-or-create-digest.usecase.ts:82-124](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L82-L124)):
     - Updates child jobs to `status: JobStatusEnum.MERGED`.
     - Updates the digest job with `status: MERGED` and `_mergedDigestId = activeDigestId` (Event 1's ID).
     - Updates the notification record with `_digestedNotificationId = activeNotificationId` (Event 1's notification ID).
   - In `AddJob` (lines 284-290), `isShouldHaltJobExecution(DigestCreationResultEnum.MERGED)` evaluates to `true`! **Execution halts immediately** for events 2-10; no downstream email or in-app jobs are scheduled for them.

3. **5-Minute Delay Expires on BullMQ**:
   - BullMQ releases Event 1's job to `StandardWorker` -> `RunJob.execute()` ([run-job.usecase.ts:93-140](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/run-job/run-job.usecase.ts#L93-L140)).
   - `RunJob` calls `SendMessage.execute()` with `stepType === StepTypeEnum.DIGEST`, routing to `Digest.execute()` ([digest.usecase.ts:48-118](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/digest/digest.usecase.ts#L48-L118)).
   - `Digest.getEvents()` queries MongoDB:
     ```ts
     const jobs = await this.jobRepository.find({
       _mergedDigestId: currentJob._id,
       status: JobStatusEnum.MERGED,
       type: StepTypeEnum.DIGEST,
       _environmentId: currentJob._environmentId,
       _subscriberId: command._subscriberId,
     }, '_id _notificationId createdAt payload');
     return [currentJob, ...jobs].map(buildDigestEvent);
     ```
     (lines 105-118).
   - This resolves all 10 events (Event 1 + the 9 merged jobs).
   - Updates downstream jobs:
     ```ts
     await this.jobRepository.update(
       { _environmentId: command.environmentId, _id: { $in: jobsToUpdate } },
       { $set: { 'digest.events': events } }
     );
     ```
     (lines 80-92).
   - Once completed, `RunJob` triggers `QueueNextJob` ([queue-next-job.usecase.ts:16-42](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/queue-next-job/queue-next-job.usecase.ts#L16-L42)) which loads the next step (`_parentId = currentJob._id`, e.g., Email or In-App).
   - The downstream notification step renders with `step.digest.events` containing all 10 events.
   - **Result**: Exactly 1 digest notification delivered containing all 10 events.

---

## 5. Killer Test #2: Email Muted -> In-App Notifications Only

### Observation & Execution Trace:
- **Where**:
  - [send-message.usecase.ts:123-148, 342-439, 561-582](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message.usecase.ts#L123-L148)
  - [get-subscriber-template-preference.usecase.ts:44-83](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/get-subscriber-template-preference/get-subscriber-template-preference.usecase.ts#L44-L83)
  - [preferences.schema.ts:36-80](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/preferences/preferences.schema.ts#L36-L80)

### Detailed Flow:
1. **Subscriber Preference Configuration**:
   - The subscriber updates their channel preference (e.g., via `PATCH /v1/subscribers/:subscriberId/preferences/:templateId`).
   - Stored in `preferences` collection with schema:
     ```ts
     preferences: {
       channels: {
         email: { enabled: false },
         in_app: { enabled: true }
       }
     }
     ```
     ([preferences.schema.ts:48-80](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/preferences/preferences.schema.ts#L48-L80)).

2. **Evaluation During Workflow Step Execution**:
   - In a multi-channel workflow containing both an `EMAIL` step and an `IN_APP` step, each step executes as a discrete job in the chained workflow.
   - When the **EMAIL job** runs:
     - `RunJob.execute()` invokes `SendMessage.execute()` ([send-message.usecase.ts:94](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message.usecase.ts#L94)).
     - Calls `this.evaluateFilters(command, variables)` (line 123), which calls `this.evaluateChannelPreference(command, compileContext)` (lines 342-439).
     - Resolves preferences via `GetSubscriberTemplatePreference.execute()` (lines 384-398).
     - Evaluates channel preference using `stepPreferred(preference, job)`:
       ```ts
       private stepPreferred(preference: { enabled: boolean; channels: IPreferenceChannels }, job: JobEntity) {
         const workflowPreferred = preference.enabled;
         const channelPreferred = Object.keys(preference.channels || {}).some(
           (channelKey) => channelKey === job.type && preference.channels?.[job.type]
         );
         return workflowPreferred && channelPreferred;
       }
       ```
       ([send-message.usecase.ts:561-569](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message.usecase.ts#L561-L569)).
     - For the EMAIL step: `job.type === 'email'`, but `preference.channels['email'] === false`.
     - `stepPreferred` returns `false`!
     - `evaluateChannelPreference` logs `Skipped step by preference` and creates execution details `STEP_FILTERED_BY_SUBSCRIBER_WORKFLOW_PREFERENCES` (lines 414-436).
     - `SendMessage.execute()` detects `!preferenceShouldRun` and immediately returns:
       ```ts
       return {
         status: SendMessageStatus.SKIPPED,
         deliveryLifecycleState: {
           status: DeliveryLifecycleStatusEnum.SKIPPED,
           detail: DeliveryLifecycleDetail.SUBSCRIBER_PREFERENCE,
         },
       };
       ```
       (lines 138-148).
     - No email is sent.
     - `RunJob` receives `SKIPPED`, updates job status to `CANCELED`/`SKIPPED`, and queues the next job (`QueueNextJob`).

3. **When the IN_APP job runs**:
   - `SendMessage.execute()` runs `evaluateChannelPreference` with `job.type === 'in_app'`.
   - `preference.channels['in_app'] === true`.
   - `stepPreferred` returns `true`.
   - `SendMessage.execute()` reaches the switch statement:
     ```ts
     case StepTypeEnum.IN_APP: {
       return await this.sendMessageInApp.execute(sendMessageChannelCommand);
     }
     ```
     (line 198).
   - In-app notification is persisted to `messages` collection and broadcasted via WebSockets (`SendMessageInApp.execute`, lines 218-241).
   - **Result**: The subscriber receives only the in-app notification; the email is completely suppressed.

---

## 6. Killer Test #3: Failed Send Retried Without Duplicate Notifications

### Observation & Execution Trace:
- **Where**:
  - [idempotency.interceptor.ts:79-125](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/shared/framework/idempotency.interceptor.ts#L79-L125)
  - [trigger-event.usecase.ts:390-404](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts#L390-L404)
  - [job.repository.ts:91-108](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.repository.ts#L91-L108)
  - [standard.worker.ts:238-288](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/standard.worker.ts#L238-L288)
  - [run-job.usecase.ts:899-901](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/run-job/run-job.usecase.ts#L899-L901)
  - [send-message-in-app.usecase.ts:175-255](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-in-app.usecase.ts#L175-L255)
  - [send-message-email.usecase.ts:207-228, 683-732](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-email.usecase.ts#L207-L228)

### Detailed Analysis of Idempotency & Retries in Novu:

1. **Trigger-Level Duplicate Prevention**:
   - Two mechanisms exist:
     - **API Idempotency Header**: Handled by `IdempotencyInterceptor` ([idempotency.interceptor.ts:79-125](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/api/src/app/shared/framework/idempotency.interceptor.ts#L79-L125)). Caches request body hash in Redis for 24h. Returns 409 Conflict if in-progress; returns cached response if completed.
     - **Transaction ID Uniqueness**: Handled by `TriggerEvent.validateTransactionIdProperty` ([trigger-event.usecase.ts:390-404](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts#L390-L404)). Queries `JobRepository.findOne({ transactionId, _environmentId })`. Throws 400 Bad Request if already seen.

2. **Worker Execution Concurrency Control (Atomic Lease Claim)**:
   - When a worker picks up a job from BullMQ, it executes `JobRepository.claimAsRunning` ([job.repository.ts:91-108](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.repository.ts#L91-L108)):
     ```ts
     return this.findOneAndUpdate(
       {
         _environmentId: environmentId,
         _id: jobId,
         $or: [
           { status: { $in: [JobStatusEnum.QUEUED, JobStatusEnum.DELAYED] } },
           { status: JobStatusEnum.RUNNING, updatedAt: { $lte: this.staleClaimCutoff() } },
         ],
       },
       { $set: { status: JobStatusEnum.RUNNING } },
       { new: true }
     );
     ```
   - If another worker already claimed the job, `findOneAndUpdate` returns `null`, preventing duplicate parallel execution ([run-job.usecase.ts:126-136](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/run-job/run-job.usecase.ts#L126-L136)).

3. **In-App Deduplication on Retry / Redelivery**:
   - `SendMessageInApp` implements deliberate message deduplication ([send-message-in-app.usecase.ts:175-255](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-in-app.usecase.ts#L175-L255)):
     - Checks if a message already exists for `_notificationId`, `_subscriberId`, `_templateId`, `_messageTemplateId`, `transactionId`, `channel: IN_APP`.
     - If `oldMessage` exists, it performs `findOneAndUpdate` on the existing message ID instead of inserting a duplicate!
     - Only if `!oldMessage` does it call `messageRepository.create()`.

4. **Critical Asymmetry & Limitation in Original Code**:
   - While `SendMessageInApp` deduplicates against `oldMessage`, `SendMessageEmail` ([send-message-email.usecase.ts:207-228](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-email.usecase.ts#L207-L228)) directly calls `this.messageRepository.create()` without checking for an existing message.
   - Furthermore, `StandardWorker.jobHasFailed` ([standard.worker.ts:248-288](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/standard.worker.ts#L248-L288)) only triggers BullMQ retry if `hasToBackoff` is true, which strictly depends on `runJob.shouldBackoff(error)`:
     ```ts
     public shouldBackoff(error: Error): boolean {
       return isRetryableWebhookFilterError(error);
     }
     ```
     ([run-job.usecase.ts:899-901](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/run-job/run-job.usecase.ts#L899-L901)).
   - Provider delivery errors (email/SMS network failures, 5xx gateway errors) are caught in `send-message-email.usecase.ts:683-732`, return `SendMessageStatus.FAILED`, and the workflow is marked `FAILED` without automated retry!
   - This exact limitation provides crucial evidence for our GAPS analysis and clean-room rebuild architecture.

---

## 7. Relevant Database & Storage Structures

From `libs/dal/src/repositories/`:

1. **`Notification`** ([notification.schema.ts:18-70](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/notification/notification.schema.ts#L18-L70)):
   - `_environmentId`, `_organizationId`, `_subscriberId`, `_templateId`
   - `transactionId`: Unique transaction ID from trigger
   - `channels`: Array of channel types (`in_app`, `email`, etc.)
   - `_digestedNotificationId`: Points to active digest notification when merged into a digest

2. **`Job`** ([job.schema.ts:32-90, 410-425](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.schema.ts#L32-L90)):
   - `_notificationId`, `_subscriberId`, `_templateId`, `_environmentId`, `_organizationId`
   - `_parentId`: Points to predecessor job in workflow chain
   - `status`: `pending`, `queued`, `running`, `completed`, `failed`, `delayed`, `canceled`, `merged`, `skipped`
   - `type`: `in_app`, `email`, `sms`, `chat`, `push`, `digest`, `delay`, `trigger`
   - `digest`: Contains `{ type, amount, unit, backoff, digestKey, digestValue, events }`
   - `_mergedDigestId`: Points to the master digest job if this job was merged into a digest window
   - `payload`: Trigger payload data

3. **`Message`** ([message.schema.ts:25-95](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/message/message.schema.ts#L25-L95)):
   - `_notificationId`, `_subscriberId`, `_environmentId`, `_organizationId`, `_jobId`
   - `channel`: `in_app`, `email`, etc.
   - `content`, `subject`, `cta`, `seen`, `read`, `deliveredAt`
   - `transactionId`, `providerId`, `errorId`, `status`

4. **`Preferences`** ([preferences.schema.ts:9-92](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/preferences/preferences.schema.ts#L9-L92)):
   - `_environmentId`, `_organizationId`, `_subscriberId`, `_templateId`
   - `type`: `subscriber_workflow`, `subscriber_global`, `workflow_resource`
   - `preferences.all.enabled`: Boolean
   - `preferences.channels`: `{ email: { enabled }, in_app: { enabled }, sms: { enabled }, ... }`

---

## 8. Summary of Discovered Flows vs Challenge Requirements

| Requirement / Flow | Discovered In Original Repository | Evidence | Clean-Room Rebuild Implication |
|---|---|---|---|
| **Event Ingestion** | `POST /v1/events/trigger` parses command, validates schema, pushes to BullMQ `workflow` queue | `events.controller.ts:88-135`, `parse-event-request.usecase.ts:422` | API receives event, returns 202/201 + transactionId, writes to internal queue |
| **Recipient Fan-out** | `TriggerMulticast` splits single/topic recipients and enqueues to `subscriber-process` queue | `trigger-multicast.usecase.ts:58`, `trigger-base.usecase.ts:61` | Direct fan-out to subscriber notification jobs |
| **Digest Grouping (Killer Test 1)** | First event creates delayed job in BullMQ; subsequent 9 events within window set `status: MERGED` and halt; master delay expiry queries merged jobs by `_mergedDigestId` | `merge-or-create-digest.usecase.ts:161-177`, `add-job.usecase.ts:284-298`, `digest.usecase.ts:105-118` | Implement delayed queue + atomic digest window grouping by subscriber + workflow |
| **Channel Preferences (Killer Test 2)** | `stepPreferred` checks `preference.channels[job.type]`; if false, returns `SKIPPED`, suppressing email while in-app step proceeds | `send-message.usecase.ts:138-148, 561-569`, `preferences.schema.ts:48-80` | Check subscriber channel preferences before executing each step job; skip muted channel, proceed on unmuted |
| **Retries & De-duplication (Killer Test 3)** | Atomic claim in `JobRepository.claimAsRunning`; in-app de-duplicates via `_notificationId` + `channel` lookup; email lacks idempotency lookup; retries limited to webhook filters | `job.repository.ts:91-108`, `send-message-in-app.usecase.ts:175-255`, `run-job.usecase.ts:899-901` | Implement robust exponential retry for provider sends + idempotent message upsert for all channels |
