# HUTCH Resolve

This repository contains the synthetic telecom sandbox, Resolve backend, conversation controller and combined customer/agent frontend for the HUTCH Resolve hackathon entry. The environment is synthetic; live browser Voice/model and release qualification are still pending.

## Team implementation plan

Start with [context.md](context.md), the agent-maintained source of truth, and the owner plans for [Harry](docs/plans/harry.md), [Jayith](docs/plans/jayith.md), and [Tevin](docs/plans/tevin.md). [Shared contracts](docs/contracts.md) distinguish implemented APIs from planned or externally dependent behavior.

## Start and inspect

1. Copy `.env.example` to `.env` and change the local development passwords if desired.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`.
3. On a fresh Docker volume, PostgreSQL 18 applies both SQL migrations and loads the initial run automatically.
4. Adopt the existing SQL baseline and application migrations with `python -m alembic upgrade head` (the migration connection uses `MIGRATION_DATABASE_URL`). Connect with any PostgreSQL client at `localhost:55432`, database `hutch_resolve`, user `hutch_admin`. The sandbox account is `hutch_sandbox`; the application account is `hutch_resolve_app`.
5. After migrations are current, run `powershell -ExecutionPolicy Bypass -File scripts/reset.ps1` to create a new active fixture run. The reset atomically retires earlier runs and revokes existing sessions/Voice bindings while retaining historical rows. `scripts/stop.ps1` stops the database without removing its Docker volume.

## Start the backend foundation

The backend exposes process liveness/readiness, anonymous/demo session lifecycle, customer-scoped account and case reads, persisted A/D ledger, B DATA_DEPLETION, C CONNECTIVITY, E captured-payment and F VAS dispute investigations, action proposal/confirmation, operation polling, Trust Receipts, agent review APIs and text conversations. The in-process conversation controller also handles authenticated Voice callbacks through the Resolve-owned bridge. The B fixture keeps byte depletion and the out-of-bundle LKR debit in separate calculations. E distinguishes payment capture, fulfilment and ledger credit and does not advise a second payment. F does not infer consent from a missing activation record; a future VAS renewal stop remains separate from past-charge review. The C investigation requires fresh matching checks/incidents and does not infer healthy service or an unsupplied ETA. Accepted actions and agent review-ticket sync run through an in-process leased worker and a separate mock sandbox-write connection. Without `SANDBOX_DATABASE_URL`, confirmed actions and ticket sync remain pending, with no success claim. Demo logins are disabled until `DEMO_IDENTITIES_JSON` is configured with credential hashes and fixed synthetic run/account IDs. Provider results are synthetic and never indicate a real HUTCH system change. Set Resolve's `VOICE_HMAC_SECRET` to the same value as Voice's `HUTCH_RESOLVE_HMAC_SECRET`, set `VOICE_BASE_URL`, and allow the configured browser origin in Voice. Replace the `.env.example` application secret before starting the server; use `APP_COOKIE_SECURE=true` under HTTPS.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn backend.resolve.app.main:app --host 127.0.0.1 --port 8080
```

The application reads `DATABASE_URL` and `SANDBOX_DATABASE_URL` from `.env`; Resolve uses `hutch_resolve_app` for business state and the separate `hutch_sandbox` role for synthetic writes and configured one-shot fault controls. `GET /api/v1/healthz` checks process liveness. `GET /api/v1/readyz` checks PostgreSQL and the current schema head `0009_package_activation`. Alembic uses the local admin `MIGRATION_DATABASE_URL`; revision 0001 validates/adopts schemas 001-003, and revisions 0002-0009 add lifecycle, investigation, proposal/confirmation, review sync, Voice consent fencing, conversation persistence, audit remediation and package activation. Opt-in PostgreSQL tests verify action recovery, review sync, text replay, guest upgrade and signed Voice confirmation.

## Start the frontend

From `frontend`, run `npm ci` and `npm run dev`. The Vite app serves customer chat/call at `/` and the agent dashboard at `/agent`; `/api` proxies to Resolve on port 8080. `VITE_API_MODE=live` is the default. Use `VITE_API_MODE=mock` only for visibly synthetic UI development. The browser Voice panel needs the separately running Zeptaz Voice service and its configured Resolve HMAC and browser Origin settings. See [frontend/README.md](frontend/README.md) for browser setup and known limits.

Reset accepts an optional UUID: `scripts/reset.ps1 -RunId <uuid>`. Fixture IDs are deterministically derived under that run, so repeatable inputs produce repeatable records and different runs do not collide. A duplicate run UUID fails transactionally without retiring the current run or revoking its sessions.
HTTP logs are compact JSON with request ID, route template, method, status, elapsed time and a stable error code. Request/response bodies, headers, query values and exception text are omitted.

The database volume is Docker-managed, outside the OneDrive-synced repository. `docker compose down -v` permanently removes local database history.

## Local demo database and tests (macOS/Linux)

- `sh scripts/dev_db.sh reset --demo` recreates a disposable PostgreSQL on `127.0.0.1:55434` (container `hutch-dev-db`, never the shared compose database), migrates it to head, clears the seeded faults and arms one CRM outage. It prints the URLs for `.env`. Restart the backend afterwards.
- A fresh fixture run arms 13 one-shot faults (late and duplicate postings, VAS write failures, a CRM outage), so the first journeys after a reset are deliberately imperfect. `python scripts/demo_faults.py list` shows them; `clear` gives a predictable happy path; `arm crm-outage` (or `crm-lost-response`, `vas-rejected`, ...) prepares exactly the failure to show.
- `sh scripts/run_db_tests.sh` runs every opt-in PostgreSQL test file on its own freshly reset database (a separate `hutch-test-db` on port 55435, never the app's database) with the derived fixture runs the tests expect. The default `pytest` run skips all of them, so run this before calling the backend verified.
- Demo phrasing: "this morning" and "today" resolve to the 2 October fixture day; "yesterday" means 1 October and finds no events.
- Languages: the interface is English, Sinhala and Tamil. Case replies stay English until a fluent reviewer approves `backend/resolve/conversation/locales/si.json` and `ta.json` (see `LANGUAGE_REVIEW.md`); say so if asked.

## HubSpot CRM (optional)

Review tickets can go to a real HubSpot account instead of the mock CRM. Only synthetic case references and summaries are sent; sync is one-way (Resolve to HubSpot).

1. In HubSpot, create a Service Key (Settings, Integrations, Service Keys) with ticket read/write and ticket schema read/write. Put it in `.env` as `HUBSPOT_ACCESS_TOKEN`; never commit or share it.
2. `python scripts/hubspot_setup.py check --write-env` writes the Hub ID, web domain, pipeline and stage IDs to `.env`; `properties` creates the `resolve_*` ticket fields; `spike --pause` runs a live create/duplicate/note/stage test and archives its tickets.
3. Set `CRM_PROVIDER=hubspot` and restart. The backend checks the key, stages and properties at startup and logs a warning if anything is missing.
4. Optional agent link: set `VITE_CRM_NAME=HubSpot` and `VITE_CRM_TICKET_URL=https://<web domain>/contacts/<hub id>/record/0-5/{id}` in `frontend/.env.local`.

Fallback: `CRM_PROVIDER=mock` and a restart restore the built-in mock CRM. Details, limits and verification: [docs/plans/hubspot-crm.md](docs/plans/hubspot-crm.md).

## Repository map

- `database/migrations/001_sandbox.sql`: synthetic CRM, charging, recharge, product/VAS, usage/quota and service-assurance records.
- `database/migrations/002_resolve.sql` and `003_scope_constraints.sql`: initial Resolve persistence and cross-run ownership constraints.
- `database/seed.sql`: fixture version 2 with six deterministic prepaid support cases and provider fault profiles.
- `backend/resolve/app/`: FastAPI startup, health/readiness, error envelope, session lifecycle, auth context, and account endpoint.
- `backend/resolve/providers/`: vendor-neutral PostgreSQL adapter and deterministic ledger reconciliation function; `crm.py` (external CRM port) and `hubspot.py` (optional HubSpot adapter).
- `backend/resolve/migrations/`: Alembic environment, non-destructive baseline adoption, lifecycle schema and case-investigation persistence.
- `scripts/`: start/stop/reset (PowerShell), fixture UUID generation, `dev_db.sh` and `run_db_tests.sh` (macOS/Linux), `demo_faults.py` (fault control) and `hubspot_setup.py` (HubSpot setup and spike).
- `docs/mock-environment.md`: relationships, assumptions, failure modes and future provider contracts.

## Boundaries and security

All customer names, line aliases, transactions, offers, usage and incidents are synthetic. No real telephone numbers, HUTCH credentials, production system access, or actual vendor-specific schema is represented. `.env` is ignored by Git. The published Compose port is local development infrastructure; do not expose it to a network.

The reviewed master architecture is `HUTCH_Resolve_System_Architecture_Reviewed.pdf` in the competition workspace. HUTCH integration access is unavailable for the hackathon; provider contracts in the docs describe a future application implementation, not deployed services here.
