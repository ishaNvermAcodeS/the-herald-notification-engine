# Gap Analysis & Engineering Weaknesses in Original Novu

## 1. Overview

During source-code inspection of the Novu repository, particularly:

- `apps/api`
- `apps/worker`
- `libs/application-generic`
- `libs/dal`

we identified several areas where the observed implementation presents potential reliability, scalability, or user-experience weaknesses.

This document records the source evidence for each candidate gap, explains the engineering implication, and identifies one candidate as the Fix for the clean-room rebuild.

> **Evidence standard:** A source-code observation is distinguished from the engineering conclusion derived from that observation. Where the repository does not directly prove a failure under concurrency or production load, the issue is described as a risk rather than as an experimentally confirmed defect.

---

## 2. Candidate Gaps

### Candidate Gap 1: Non-Atomic Digest Master Election

**Category:** Concurrency / consistency

**Source Evidence**

`apps/worker/src/app/workflow/usecases/add-job/merge-or-create-digest.usecase.ts:161-177`

The digest logic first searches for an existing delayed digest job and, when none is found, subsequently marks the current job as the digest master.

```ts
const delayedDigestJob =
  await this.jobRepository.getExistingDelayedJobWithTheSameDigestValue(
    job,
    digestMeta,
  );

if (!delayedDigestJob) {
  await this.jobRepository.markJobAsDigestMaster(job);

  return {
    activeDigestId: job._id,
    digestResult: DigestCreationResultEnum.CREATED,
  };
}
