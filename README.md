# The Herald — Clean-Room Campus Notification Engine

The Herald is an event-driven campus notification engine: a burst of campus events becomes the
*right* notifications — filtered by each subscriber's preferences, grouped into digests, delivered
reliably (real email + persistent in-app), retried without duplicates, and summarised by an LLM.

It is a **clean-room implementation**. Novu was studied only as a behavioural reference (see `docs/`);
The Herald does not depend on, call, or copy Novu, and it is **not** a complete Novu replacement —
only the notification-engine slice relevant to this challenge is built.

## What we built on top of the reference

| | |
|---|---|
| **Fix — Atomic Digest Window Ownership** | The original elects the digest master with check-then-write, which can create duplicate masters under concurrency (docs/GAPS.md §1, §4). Ours is a single atomic insert against a partial unique index; see `test_concurrent_events_elect_exactly_one_master`. |
| **Differentiator — AI Smart Digest** | Digests become a TL;DR + action items (Groq `openai/gpt-oss-20b`), validated, with automatic fallback to a plain digest when AI is unavailable (docs/GAPS.md §5). |

Reverse-engineering reference: Novu (<https://github.com/novuhq/novu>, commit `5c7191d`), studied only to write
`docs/`; the implementation here was built from those docs. See `SUBMISSION.md` for the hackathon summary.

## Architecture

```
Admin/Student UI (Next.js)  ──proxy (adds API key server-side)──▶  FastAPI  /v1/...
                                                                      │ enqueue
                                                                      ▼
                                                            Redis  herald:queue:workflow-jobs
                                                                      ▼
                                              Worker ── WorkflowExecutor ── Notification + chained Jobs (per recipient)
                                                 │
                                                 └─ JobRunner
                                                      DIGEST   atomic master election / merge (PostgreSQL)
                                                      EMAIL / IN_APP  preference check → stable Message → provider
                                                      retry    exponential backoff, persisted in Job.delayUntil
                                                      AI       DigestSynthesizer (LLM, validated, falls back to plain digest)
                                                                      ▼
                                                                 PostgreSQL
```

Design notes
- **Atomic digest ownership** — master election is one `INSERT … ON CONFLICT DO NOTHING` on the partial
  unique index `digest_windows(subscriberId, workflowId, digestKey) WHERE status='OPEN'`; losers merge via an
  atomic `UPDATE eventCount+1`. No check-then-insert. Window expiry is an atomic `OPEN→EXPIRED` claim.
- **Idempotency** — event: per-recipient Notification `transactionId` (`<tx>:<subscriber>`); digest: a
  notification attaches to a window once; message: deterministic `deduplicationKey` (unique) shared by all
  retries; delivery: a `SENT` Message is never re-sent, and the provider gets the same `Idempotency-Key`.
- **Delayed work** (digest windows, retry backoff) is stored in PostgreSQL and polled by the worker; Redis
  carries the ingest queue. Delayed work therefore survives worker restarts.
- Emergency (`isCritical`) workflows override channel mutes.

## Setup

Prereqs: Python 3.11+, PostgreSQL 16, Redis, Node 20+.

```bash
cp .env.example .env            # then edit (never commit .env)
createdb the_herald
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
alembic upgrade head && python -m app.db.seed      # schema + default workflows
cd ../frontend && cp .env.local.example .env.local && npm install
```

### Run (5 terminals / services)
```bash
brew services start postgresql@16 ; brew services start redis      # 1-2: PostgreSQL, Redis
cd backend && uvicorn app.main:app --port 8000                      # 3: API
cd backend && python worker.py                                      # 4: worker
cd frontend && npm run dev                                          # 5: UI  → http://localhost:3000
```
Phone demo: open `http://<laptop-LAN-IP>:3000/student` (Next binds to 0.0.0.0).

## Configuration (`.env`)
| Variable | Purpose |
|---|---|
| `API_KEY` | Herald API key (Bearer / `x-api-key`) |
| `DATABASE_URL`, `REDIS_URL` | local PostgreSQL / Redis |
| `EMAIL_PROVIDER` | `resend` (real, default) or `mock` (tests only) |
| `EMAIL_API_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME` | Resend credentials / sender |
| `DELIVERY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY_SECONDS` | retry bound; delay = base·2^(attempt-1) |
| `DIGEST_WINDOW_MS_OVERRIDE` | shorten the 5-minute digest window for demos |
| `AI_PROVIDER` | `groq` (real, default) or `mock` |
| `AI_API_KEY`, `AI_MODEL`, `AI_TIMEOUT_SECONDS` | LLM credentials / model |

Provider keys live only in the backend `.env`; the browser never sees them (the Next.js route
`/api/herald/*` proxies to the API and injects the Herald API key server-side).

### Real email (Resend)
1. Create a key at <https://resend.com/api-keys>; set `EMAIL_API_KEY` in `.env`.
2. Without a verified domain Resend only delivers to your own account address using
   `EMAIL_FROM=onboarding@resend.dev`; verify a domain to email others.
3. Give a subscriber a real address: Admin → Subscribers, or
   `PUT /v1/subscribers/alice {"email": "you@example.com"}`; then trigger an event for `alice`.
4. Admin → "Notifications, jobs & delivery" shows the Message status and retry attempts.

### AI digest
Set `AI_API_KEY` (Groq, https://console.groq.com/keys). Trigger ≥2 `campus-bulletin` events for one subscriber (Admin → "Send burst ×5");
when the window closes the digest gets `TL;DR` + `action_items`. Missing key / timeout / malformed output →
automatic fallback to a plain digest (the notification is still delivered; the admin view shows why).

## API (all under `/v1`, API key required)
- `POST /events/trigger`, `POST /events/trigger-bulk` — ingest (202, async)
- `GET/PUT /subscribers`, `PUT /subscribers/{id}`, `GET/PUT /subscribers/{id}/preferences`
- `GET /notifications?subscriberId=`, `GET /notifications/unread?subscriberId=`, `POST /notifications/{id}/read?subscriberId=`
- `GET /admin/overview | /admin/notifications | /admin/digests | /admin/jobs`

## Testing
```bash
cd backend && pytest                 # needs local PostgreSQL + Redis, no external credentials
cd frontend && npm run typecheck && npm run build
```
The app runs without any API keys: with no `EMAIL_API_KEY` email steps fail visibly (in-app still works), and with no `AI_API_KEY` digests fall back to the plain format.
Mock providers are used **only** in tests. Killer tests: `test_killer_test_1_ten_events_one_digest`,
`test_killer_test_2_email_disabled_in_app_still_delivered`, `test_killer_test_3_retry_without_duplicates`;
race test: `test_concurrent_events_elect_exactly_one_master`.

## Limitations
- A worker crash mid-delivery can leave a Job `RUNNING` / a window `EXPIRED` (no reaper yet); provider-side
  idempotency keys prevent a duplicate email on manual re-run.
- Ingestion idempotency cache (`Idempotency-Key`) is in-process memory. Digest `digestKey` comes only from
  `payload.digestKey` (default: one window per subscriber+workflow). Polling UI, no WebSockets. Single-tenant.
