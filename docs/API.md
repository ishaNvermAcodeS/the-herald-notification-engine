# Clean-Room API Specification

## 1. Overview & Conventions

This API defines the external and internal HTTP interfaces for The Herald Campus Notification Engine.

### Base Configuration

- **Base Path:** `/v1`
- **Format:** JSON
- **Content-Type:** `application/json`
- **Authentication:** Bearer API token
- **Asynchronous Processing:** Event triggers are queued and processed by background workers.
- **Idempotency:** Supported through `transactionId` and request-level `Idempotency-Key`.

### Authentication

Requests requiring authentication must provide:

```http
Authorization: Bearer <API_KEY>
