# Agent Reverse-Engineering Log

## 1. Investigation Log & Repository Exploration

### Timeline & Methodology
The reverse-engineering process was conducted through systematic code inspection of the local Novu codebase at `/Users/ishanverma/Desktop/Debug_Hack/novu`. No external cloning or guessing was performed.

### Directories & Modules Inspected
1. **`apps/api/src/app`**:
   - `events/events.controller.ts` & `events/usecases/parse-event-request`: Inspected HTTP ingestion layer, transaction ID generation, recipient parsing, and dispatch to `workflowQueueService`.
   - `subscribers/subscribersV1.controller.ts`: Inspected subscriber preference updates (`PATCH /preferences`), in-app feed retrieval (`GET /notifications/feed`), and read status updates.
   - `shared/framework/idempotency.interceptor.ts`: Inspected the API-level Redis idempotency interceptor.

2. **`apps/worker/src/app/workflow`**:
   - `services/workflow.worker.ts`: Traced trigger job consumption and `TriggerEvent` invocation.
   - `services/subscriber-process.worker.ts`: Traced subscriber creation and job generation via `SubscriberJobBound`.
   - `services/standard.worker.ts`: Analyzed job completion, failure handling (`jobHasFailed`), and retry limitations (`shouldBackoff`).
   - `usecases/add-job/add-job.usecase.ts`: Analyzed delayed job scheduling, condition filtering, defer duration checks, and BullMQ enqueueing.
   - `usecases/add-job/merge-or-create-digest.usecase.ts`: Analyzed digest master election and merged event handling.
   - `usecases/run-job/run-job.usecase.ts`: Analyzed atomic lease claim (`claimAsRunning`), subscriber quiet-hour extensions, and step dispatch.
   - `usecases/send-message/send-message.usecase.ts`: Analyzed channel preference evaluation (`evaluateChannelPreference`, `stepPreferred`) and skip logic.
   - `usecases/send-message/digest/digest.usecase.ts`: Traced event aggregation on digest timer completion (`getEvents`).
   - `usecases/send-message/send-message-email.usecase.ts` & `send-message-in-app.usecase.ts`: Inspected provider delivery, message creation, and deduplication behavior.

3. **`libs/application-generic/src`**:
   - `usecases/trigger-event/trigger-event.usecase.ts`: Inspected transaction ID uniqueness check (`validateTransactionIdProperty`).
   - `usecases/trigger-multicast/trigger-multicast.usecase.ts`: Inspected recipient batching and fan-out.
   - `usecases/create-notification-jobs/create-notification-jobs.usecase.ts`: Inspected notification creation, step filtering, and digest metadata binding.
   - `usecases/get-subscriber-template-preference/get-subscriber-template-preference.usecase.ts`: Inspected preference resolution hierarchy.

4. **`libs/dal/src/repositories`**:
   - `job/job.repository.ts`: Inspected `storeJobs` parent-child chaining and `claimAsRunning` atomic state transitions.
   - `preferences/preferences.schema.ts`: Inspected preference data model.
   - `notification/notification.schema.ts` & `message/message.schema.ts`: Inspected schema definitions.

---

## 2. Key Questions Investigated & Evidence Discovered

### Q1: How does Novu guarantee that 10 events within 5 minutes become 1 digest?
- **Finding**:
  1. The first event's digest job enters `MergeOrCreateDigest.execute` ([merge-or-create-digest.usecase.ts:161-177](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L161-L177)). It finds no active delayed job, marks itself as master (`DigestCreationResultEnum.CREATED`), and schedules a 5-minute delay in BullMQ via `StandardQueueService.add({ delay: 300000 })` ([add-job.usecase.ts:385, 1107](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/add-job.usecase.ts#L385)).
  2. Events 2 through 10 find the active delayed job, update their status to `JobStatusEnum.MERGED`, set `_mergedDigestId = activeDigestId`, and halt execution ([add-job.usecase.ts:284-290](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/add-job.usecase.ts#L284-L290)).
  3. When the 5-minute timer expires, `Digest.getEvents` ([digest.usecase.ts:105-118](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/digest/digest.usecase.ts#L105-L118)) queries all jobs with `_mergedDigestId = currentJob._id`, collects all 10 event payloads, and passes them to downstream channel jobs.

### Q2: How does a muted email preference suppress email while allowing in-app notifications?
- **Finding**:
  1. In `SendMessage.execute` ([send-message.usecase.ts:123-148](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message.usecase.ts#L123-L148)), before dispatching to any channel, `evaluateChannelPreference` is called.
  2. It evaluates `stepPreferred(preference, job)`:
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
  3. For Email, `preference.channels['email'] === false`, so it returns `false`. `SendMessage` returns `{ status: SKIPPED, detail: 'subscriber_preference' }`, bypassing the email provider.
  4. For In-App, `preference.channels['in_app'] === true`, so it returns `true`, and `sendMessageInApp.execute` successfully stores the notification in the subscriber's feed.

### Q3: How does retry and deduplication work when a send fails?
- **Finding**:
  1. **Concurrency Control**: `JobRepository.claimAsRunning` ([job.repository.ts:91-108](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/dal/src/repositories/job/job.repository.ts#L91-L108)) uses an atomic MongoDB `findOneAndUpdate` to transition jobs to `RUNNING`. A second worker cannot execute the same job in parallel.
  2. **In-App Deduplication**: `SendMessageInApp` ([send-message-in-app.usecase.ts:175-255](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-in-app.usecase.ts#L175-L255)) searches for an existing message by `(notificationId, subscriberId, templateId, transactionId)`. If found, it updates the record instead of inserting a duplicate.
  3. **Critical Asymmetry**: Novu's original `SendMessageEmail` ([send-message-email.usecase.ts:207-228](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-email.usecase.ts#L207-L228)) lacks this check and blindly creates a new `Message` record. Furthermore, `StandardWorker.jobHasFailed` ([standard.worker.ts:248-288](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/services/standard.worker.ts#L248-L288)) only retries webhook filter errors (`shouldBackoff = isRetryableWebhookFilterError`), permanently failing email sends on network drops.

---

## 3. Candidate Gaps & Why the Final Fix Was Selected

### Candidate Gaps Considered:
1. **TOCTOU Race Condition in Digest Master Election** ([merge-or-create-digest.usecase.ts:161-177](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts#L161-L177))
2. **Transaction ID Re-Trigger Throws 400 Bad Request Instead of Safe Idempotent Replay** ([trigger-event.usecase.ts:390-404](file:///Users/ishanverma/Desktop/Debug_Hack/novu/libs/application-generic/src/usecases/trigger-event/trigger-event.usecase.ts#L390-L404))
3. **Unbounded Payload Array Storage in Digest Events** ([digest.usecase.ts:80-92](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/digest/digest.usecase.ts#L80-L92))
4. **Read/Seen Inbox State Loss on In-App Message Redelivery** ([send-message-in-app.usecase.ts:243-255](file:///Users/ishanverma/Desktop/Debug_Hack/novu/apps/worker/src/app/workflow/usecases/send-message/send-message-in-app.usecase.ts#L243-L255))

### Selection of FINAL FIX:
**FINAL FIX: Atomic Digest Window Mutex (Distributed Lock for Concurrent Digest Master Election)**

**Rationale**:
- **Rooted in Code Analysis**: Identified directly in `merge-or-create-digest.usecase.ts:161-177`, where the check for existing delayed jobs and the marking of master status are two separate, non-atomic database operations.
- **Different from Killer Tests**: Killer Test 1 requires digest grouping within a 5-minute window, but assumes sequential or benign arrival. In high-concurrency event bursts, Novu's master election splits into multiple digest windows. The Fix addresses the distributed concurrency vulnerability.
- **Feasible & Demonstrable**: Implemented using an atomic Redis `SET NX EX` lease or atomic MongoDB `findOneAndUpdate` upsert on a dedicated `digest_windows` state collection. Can be demonstrated live with a 10-concurrent-trigger blast script.

---

## 4. Differentiators Considered & Selection of Final Differentiator

### Candidate Differentiators Considered:
1. **AI-Powered Smart Digest Synthesis (Campus Notification Brief)**:
   - When a 5-minute digest window closes with 10 events, synthesize them into a concise TL;DR, high-priority urgent action checklist, and categorized notices rather than dumping 10 raw JSON strings.
   - Verified that Novu does not offer this (Novu only maps raw event objects to template loops).
2. **Dynamic Emergency Safety Bypass**:
   - Automatically promotes campus safety alerts to bypass channel mutes based on NLP urgency detection.
3. **Class Schedule-Aware Quiet Hours**:
   - Suppresses alerts during lecture blocks based on university timetable integrations.

### Selection of FINAL DIFFERENTIATOR:
**FINAL DIFFERENTIATOR: AI-Powered Smart Digest Synthesis (Campus Notification Brief)**

**Rationale**:
- **Genuinely Absent in Novu**: Grep searches across Novu for AI summarization in digest confirm that Novu only performs raw payload concatenation into `step.digest.events`.
- **Solves Campus Problem**: College students suffer from alert fatigue; 10 individual emails or a wall-of-text digest are routinely ignored. A structured briefing with clear "Action Required" items directly serves campus safety and communication.
- **Feasible within Hackathon**: Can be cleanly integrated at the conclusion of the digest timer using an LLM API (Gemini/Claude) with a deterministic fallback parser.
- **Live Demo WOW Factor**: After blasting 10 campus events in 5 minutes, the recipient receives a beautifully rendered "Campus Morning Brief" highlighting urgent action items at a glance.

---

## 5. Remaining Uncertainties & Assumptions
- **Queue Engine**: Novu uses BullMQ with an SQS fallback for high-scale enterprise cloud deployments. For our clean-room rebuild, BullMQ over Redis provides identical delay, retry, and deduplication semantics with zero AWS infrastructure dependency.
- **Template Compilation**: Novu supports handlebars, React Email, and custom bridge schemas. Our clean-room implementation uses modern lightweight template rendering tailored for HTML and text output.
