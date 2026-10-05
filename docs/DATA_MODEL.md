# DATA_MODEL.md

# Clean-Room Data Model Specification
## The Herald — Campus Notification Engine

## 1. Purpose

This document defines the minimum data model required for the clean-room implementation of The Herald.

The model is derived from the notification behaviors identified during reverse engineering and from the requirements of the three Killer Tests, the selected Fix, and the Differentiator.

The objective is NOT to reproduce Novu's complete database schema.

Only entities required for the following functionality are included:

- Event ingestion and workflow execution
- Digest aggregation
- Subscriber channel preferences
- Reliable delivery and retry
- Duplicate prevention
- In-app notifications
- Atomic digest-window management
- AI-generated digest summaries

---

# 2. Entity Overview

The clean-room model contains the following primary entities:

| Entity | Purpose |
|---|---|
| Subscriber | Represents a notification recipient |
| Preference | Stores subscriber channel preferences |
| Workflow | Defines notification steps and configuration |
| Notification | Represents an incoming notification event |
| DigestWindow | Tracks an active digest aggregation window |
| Job | Represents an executable workflow step |
| Message | Represents a channel-level notification |

---

# 3. Entity Relationships

```text
Subscriber
    |
    | 1:N
    v
Preference


Subscriber
    |
    | 1:N
    v
Notification
    |
    | 1:N
    v
Job
    |
    | 1:0..1
    v
Message


Workflow
    |
    | 1:N
    +------------------+
    |                  |
    v                  v
Notification       Preference


Subscriber + Workflow + Digest Key
                |
                | 1:N
                v
          DigestWindow
                |
                | 1:N
                v
           Notification
