Team ID:   DBG-815
Team:      Noir
Card:      The Herald — Clean-Room Campus Notification Engine (Real-time & Infra)
Original:  https://github.com/novuhq/novu
Commit studied: 5c7191d
Run:       see "Run" below (PostgreSQL + Redis + `uvicorn` + `python worker.py` + `npm start`)
Improvements we built:
  1. Fix: Atomic Digest Window Ownership — the original elects the digest master with a
     check-then-write (find active delayed digest → else mark self master), so concurrent
     events can each see "no digest" and create duplicate masters / split digests.
     Ours elects the master with one atomic `INSERT … ON CONFLICT DO NOTHING` on a partial
     unique index (one OPEN window per subscriber+workflow+digestKey); losers merge via an
     atomic counter UPDATE; window expiry is an atomic OPEN→EXPIRED claim.
     Proof: `test_concurrent_events_elect_exactly_one_master` (12 simultaneous events →
     1 window, 1 master, 11 merged).
  2. Differentiator: AI Smart Digest — instead of concatenating raw events, the digest is
     synthesised into a TL;DR + prioritised action items (Groq, structured + validated JSON).
     If the key is missing, the model errors, times out or returns malformed output, the app
     falls back to a plain digest and still delivers email + in-app.
Libraries / AI used:
  - FastAPI, Uvicorn, Pydantic: async ingestion API and validated schemas
  - SQLAlchemy 2 + Alembic + asyncpg/psycopg2 + PostgreSQL 16: persistence, migrations, the atomic-election constraint
  - Redis (redis-py asyncio): ingest queue + dead-letter queue
  - httpx: Resend (real email) and Groq HTTP clients
  - Resend: real transactional email, with Idempotency-Key so a retry cannot double-send
  - Groq `openai/gpt-oss-20b`: fast, cheap, supports JSON-mode output; a short structured summary does not need a larger model
  - Next.js 15 / React 19 / TypeScript: admin + mobile-first student dashboards (server-side proxy keeps keys off the browser)
  - pytest / pytest-asyncio: 111 tests
Deck:      deck.pdf (repo root)

Killer Tests (all pass; `cd backend && pytest -k killer`):
  1. Digest grouping   — 10 events / same subscriber / 5-min window → 1 master, 9 merged, 1 consolidated notification, all 10 events preserved
  2. Preference filter — email=false, in_app=true → email SKIPPED (SUBSCRIBER_PREFERENCE), 0 provider calls, in-app SENT
  3. Retry w/o dupes   — email fails once → retried with backoff → 1 logical Message (same id), 1 successful send, 1 Notification

Run:
  cp .env.example .env            # add EMAIL_API_KEY / AI_API_KEY (optional — app runs without them)
  createdb the_herald && cd backend && pip install -r requirements.txt && alembic upgrade head && python -m app.db.seed
  uvicorn app.main:app --port 8000        # API
  python worker.py                        # worker (separate terminal)
  cd ../frontend && cp .env.local.example .env.local && npm install && npm run build && npm start
  Admin → http://localhost:3000/admin   Student → http://localhost:3000/student

Clean-room declaration: built only from our own docs/ (OBSERVATIONS, PRD, ARCHITECTURE, DATA_MODEL,
API, GAPS, AGENT_LOG). No Novu code, packages or APIs were copied or used.
