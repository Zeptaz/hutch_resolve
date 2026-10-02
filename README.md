# HUTCH Resolve — sandbox only

This repository prepares the synthetic telecom environment and the first Resolve backend foundation for the HUTCH Resolve hackathon entry. Business APIs, authorization, diagnosis, actions and customer/dashboard UIs are still under development.

## Team implementation plan

Start with [context.md](context.md), the agent-maintained source of truth, and the owner plans for [Harry](docs/plans/harry.md), [Jayith](docs/plans/jayith.md), and [Tevin](docs/plans/tevin.md). [Shared contracts](docs/contracts.md) describe upcoming application work; they are not deployed APIs. The plan records known fixture corrections and incomplete Voice qualification.

## Start and inspect

1. Copy `.env.example` to `.env` and change the local development passwords if desired.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`.
3. On a fresh Docker volume, PostgreSQL 18 applies both SQL migrations and loads the initial run automatically.
4. Adopt the existing SQL baseline and application migrations with `python -m alembic upgrade head` (the migration connection uses `MIGRATION_DATABASE_URL`). Connect with any PostgreSQL client at `localhost:55432`, database `hutch_resolve`, user `hutch_admin`. The sandbox account is `hutch_sandbox`; the application account is `hutch_resolve_app`.
5. After migrations are current, run `powershell -ExecutionPolicy Bypass -File scripts/reset.ps1` to create a new active fixture run. The reset atomically retires earlier runs and revokes existing sessions/Voice bindings while retaining historical rows. `scripts/stop.ps1` stops the database without removing its Docker volume.

## Start the backend foundation

The backend exposes process liveness/readiness, anonymous/demo session lifecycle, customer-scoped account and case reads, persisted A/D ledger, B DATA_DEPLETION, C CONNECTIVITY, E captured-payment and F VAS dispute investigations, action proposal/confirmation, operation polling, Trust Receipts, and agent review APIs. The Resolve-owned Voice bridge provisions scoped short-lived Voice grants and authenticates/deduplicates signed callbacks. Voice turns require Tevin's `ConversationService` to be installed; otherwise they safely return 503. The B fixture keeps byte depletion and the out-of-bundle LKR debit in separate calculations. E distinguishes payment capture, fulfilment and ledger credit and does not advise a second payment. F does not infer consent from a missing activation record; a future VAS renewal stop remains separate from past-charge review. The C investigation requires fresh matching checks/incidents and does not infer healthy service or an unsupplied ETA. Accepted actions and agent review-ticket sync run through an in-process leased worker and a separate mock sandbox-write connection. Without `SANDBOX_DATABASE_URL`, confirmed actions and ticket sync remain pending, with no success claim. Demo logins are disabled until `DEMO_IDENTITIES_JSON` is configured with credential hashes and fixed synthetic run/account IDs. Provider results are synthetic and never indicate a real HUTCH system change. Set Resolve's `VOICE_HMAC_SECRET` to the same value as Voice's `HUTCH_RESOLVE_HMAC_SECRET`, set `VOICE_BASE_URL`, and allow the configured browser origin in Voice. Replace the `.env.example` application secret before starting the server; use `APP_COOKIE_SECURE=true` under HTTPS.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn backend.resolve.app.main:app --host 127.0.0.1 --port 8080
```

The application reads `DATABASE_URL` and `SANDBOX_DATABASE_URL` from `.env`; Resolve uses `hutch_resolve_app` for business state and the separate `hutch_sandbox` role for synthetic writes and configured one-shot fault controls. `GET /api/v1/healthz` checks process liveness. `GET /api/v1/readyz` checks PostgreSQL and schema revision `0006_audit_hardening`. Alembic uses the local admin `MIGRATION_DATABASE_URL`; revision 0001 validates/adopts schemas 001-003 without replaying CREATE TABLE statements, revisions 0002-0006 add lifecycle, immutable investigation, proposal/confirmation, review-sync, Voice consent provenance and recovery fencing without replaying bootstrap DDL. Opt-in PostgreSQL tests verify action recovery, ordered review sync, scoped Voice consent and signed callback replay.

Reset accepts an optional UUID: `scripts/reset.ps1 -RunId <uuid>`. Fixture IDs are deterministically derived under that run, so repeatable inputs produce repeatable records and different runs do not collide. A duplicate run UUID fails transactionally without retiring the current run or revoking its sessions.
HTTP logs are compact JSON with request ID, route template, method, status, elapsed time and a stable error code. Request/response bodies, headers, query values and exception text are omitted.

The database volume is Docker-managed, outside the OneDrive-synced repository. `docker compose down -v` permanently removes local database history.

## Repository map

- `database/migrations/001_sandbox.sql`: synthetic CRM, charging, recharge, product/VAS, usage/quota and service-assurance records.
- `database/migrations/002_resolve.sql` and `003_scope_constraints.sql`: initial Resolve persistence and cross-run ownership constraints.
- `database/seed.sql`: fixture version 2 with six deterministic prepaid support cases and provider fault profiles.
- `backend/resolve/app/`: FastAPI startup, health/readiness, error envelope, session lifecycle, auth context, and account endpoint.
- `backend/resolve/providers/`: vendor-neutral PostgreSQL adapter and deterministic ledger reconciliation function.
- `backend/resolve/migrations/`: Alembic environment, non-destructive baseline adoption, lifecycle schema and case-investigation persistence.
- `scripts/`: start/stop/reset and fixture UUID generation.
- `docs/mock-environment.md`: relationships, assumptions, failure modes and future provider contracts.

## Boundaries and security

All customer names, line aliases, transactions, offers, usage and incidents are synthetic. No real telephone numbers, HUTCH credentials, production system access, or actual vendor-specific schema is represented. `.env` is ignored by Git. The published Compose port is local development infrastructure; do not expose it to a network.

The reviewed master architecture is `HUTCH_Resolve_System_Architecture_Reviewed.pdf` in the competition workspace. HUTCH integration access is unavailable for the hackathon; provider contracts in the docs describe a future application implementation, not deployed services here.
