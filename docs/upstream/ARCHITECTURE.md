# Clean-Room System Architecture: The Herald Notification Engine

## 1. Architectural Overview

The Herald is an event-driven campus notification engine designed to process high-volume events and reliably deliver notifications to students through Email and In-App channels.

The system is designed around five core capabilities:

1. **Burst Digesting** — groups multiple events into a single digest.
2. **Preference-Aware Delivery** — respects each subscriber's channel preferences.
3. **Retry & Idempotent Delivery** — retries transient failures while preventing duplicate notification records.
4. **Atomic Digest Window Ownership** — prevents concurrent workers from creating multiple digest masters for the same logical window.
5. **AI Smart Digest** — converts a burst of notifications into a concise, actionable summary.

The architecture uses a streamlined API + background-worker topology rather than reproducing the full Novu service topology.

---

## 2. High-Level Architecture

```text
                    +-----------------------------+
                    |      Campus Dispatch Client |
                    |   Admin Dashboard / API     |
                    +--------------+--------------+
                                   |
                                   | POST /v1/events/trigger
                                   v
                    +-----------------------------+
                    |        Ingestion API        |
                    |-----------------------------|
                    | Authentication              |
                    | Schema Validation            |
                    | Transaction Idempotency      |
                    +--------------+--------------+
                                   |
                                   | Enqueue
                                   v
                    +-----------------------------+
                    |       Redis / BullMQ         |
                    |-----------------------------|
                    | workflow-jobs                |
                    | delayed-jobs                 |
                    | retry jobs                   |
                    +--------------+--------------+
                                   |
                                   v
              +-------------------------------------------+
              |              Worker Service               |
              |-------------------------------------------|
              |  1. Recipient Fan-Out                     |
              |  2. Digest Engine                         |
              |  3. Preference Engine                     |
              |  4. AI Digest Synthesizer                 |
              |  5. Retry & Idempotent Delivery Engine    |
              +-------------------+-----------------------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
          +-------------------+       +----------------------+
          |   Email Provider  |       |   In-App Message DB  |
          | SMTP / Mock       |       | Notification Feed    |
          +-------------------+       +----------------------+

                         +----------------------+
                         |   Primary Database   |
                         |----------------------|
                         | Subscribers          |
                         | Preferences          |
                         | Workflows            |
                         | Digest Windows       |
                         | Notifications        |
                         | Jobs                 |
                         | Messages             |
                         +----------------------+
