# Product Requirements Document (PRD)
# The Herald — Clean-Room Campus Notification Engine

## 1. Overview

### 1.1 Product

**The Herald** is a clean-room notification engine designed for high-volume campus communication.

The system accepts events from campus systems, processes them asynchronously, groups notification bursts into digests, respects individual channel preferences, reliably retries failed deliveries, and provides an intelligent summary of grouped notifications.

### 1.2 Problem

Campus communication can fail in three major ways:

1. **Notification overload**

   High-frequency announcements such as weather alerts, parking changes, shuttle disruptions, classroom changes, and maintenance notices can generate large numbers of individual notifications.

   This creates notification fatigue and makes important information easier to miss.

2. **Preference violations**

   Students may want to mute noisy channels such as email while continuing to receive important notifications through in-app channels.

   The notification engine must respect these channel-level preferences.

3. **Delivery failures and duplicates**

   Network failures, provider outages, or temporary delivery errors can cause notifications to fail.

   Retrying without proper idempotency can result in duplicate notifications.

### 1.3 Goal

Build a notification engine that can:

- Process campus events asynchronously.
- Group bursts of events into a single digest.
- Respect per-user channel preferences.
- Retry transient delivery failures.
- Prevent duplicate notifications during retries.
- Provide a persistent in-app notification feed.
- Improve the digest experience with intelligent AI-generated summaries.

---

# 2. Target Users

## 2.1 Campus Administrator / Dispatcher

Responsible for sending campus-wide or targeted notifications.

Examples:

- Emergency announcements
- Exam timetable changes
- Classroom changes
- Transportation updates
- Maintenance notices
- Weather alerts

The administrator needs reliable delivery, predictable behavior, and visibility into notification processing.

---

## 2.2 Student / Faculty Subscriber

The recipient of campus notifications.

They should be able to:

- Receive notifications through enabled channels.
- Mute unwanted channels.
- Receive grouped digests instead of notification storms.
- View notifications through the in-app feed.

---

## 2.3 System Integrator

External university systems may generate notification events.

Examples include:

- Academic/registrar systems
- Learning management systems
- Campus transportation systems
- Campus security systems
- Facilities systems

These systems interact with the notification engine through its event API.

---

# 3. Scope

The rebuild focuses only on the notification-engine functionality required for the challenge.

### In Scope

- Event ingestion
- Asynchronous event processing
- Workflow/step execution
- Digest aggregation
- Subscriber preferences
- Email delivery
- In-app delivery
- Retry handling
- Idempotency and duplicate prevention
- In-app notification feed
- Smart AI-powered digest synthesis
- Atomic digest-window protection

### Out of Scope

The rebuild does not attempt to reproduce the entire Novu platform.

Examples of excluded functionality include:

- Enterprise billing
- SAML/SSO
- Translation suites
- Multi-tenant enterprise functionality
- MCP/agent functionality
- Other unrelated Novu platform features

---

# 4. Core Functional Requirements

## FR-1 — Event Ingestion

The system must provide an API for accepting notification events.

### Requirements

- Accept a workflow identifier.
- Accept one or more recipients.
- Accept arbitrary event payload data.
- Accept an optional transaction ID.
- Validate incoming requests.
- Process events asynchronously.
- Return an acknowledgement without waiting for final delivery.

### Example

```text
POST /v1/events/trigger
