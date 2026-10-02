# HUTCH Resolve — sandbox only

This repository prepares the synthetic telecom environment and the first Resolve backend foundation for the HUTCH Resolve hackathon entry. Business APIs, authorization, diagnosis, actions and customer/dashboard UIs are still under development.

## Team implementation plan

Start with [context.md](context.md), the agent-maintained source of truth, and the owner plans for [Harry](docs/plans/harry.md), [Jayith](docs/plans/jayith.md), and [Tevin](docs/plans/tevin.md). [Shared contracts](docs/contracts.md) describe upcoming application work; they are not deployed APIs. The plan records known fixture corrections and incomplete Voice qualification.

## Start and inspect

1. Copy `.env.example` to `.env` and change the local development passwords if desired.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`.
3. On a fresh Docker volume, PostgreSQL 18 applies both SQL migrations and loads the initial run automatically.
4. Adopt the existing SQL baseline and application migrations with `python -m alembic upgrade head` (the migration connection uses `MIGRATION_DATABASE_URL`). Connect with any PostgreSQL client at `localhost:55432`, database `hutch_resolve`, user `hutch_admin`. The sandbox account is `hutch_sandbox`; the application account is `hutch_resolve_app`.
5. Run `powershell -ExecutionPolicy Bypass -File scripts/reset.ps1` to add another isolated fixture run. Previous runs remain available. `scripts/stop.ps1` stops the database without removing its Docker volume.

## Start the backend foundation

The backend exposes process liveness/readiness, anonymous/demo session lifecycle, customer-scoped `GET /api/v1/account`, scoped case reads and idempotent investigation routes. It also exposes customer-only action proposal and confirmation routes. Confirmation records a durable `PENDING` operation but does not execute a sandbox mutation or claim success. An in-process facade supports conversation/case creation for Tevin’s conversation controller. Demo logins are disabled until `DEMO_IDENTITIES_JSON` is configured with credential hashes and fixed synthetic run/account IDs. Operation polling/execution, receipts, conversation HTTP routes and dashboard APIs remain under development. Replace the `.env.example` application secret before starting the server; use `APP_COOKIE_SECURE=true` under HTTPS.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn backend.resolve.app.main:app --host 127.0.0.1 --port 8080
```

The application reads `DATABASE_URL` from `.env`; it uses the separate `hutch_resolve_app` local role for PostgreSQL. `GET /api/v1/healthz` checks process liveness. `GET /api/v1/readyz` checks PostgreSQL and schema revision `0004_action_proposals`. Alembic uses the local admin `MIGRATION_DATABASE_URL`; revision 0001 validates/adopts schemas 001-003 without replaying CREATE TABLE statements, revisions 0002-0004 add lifecycle, immutable investigation, and proposal/confirmation persistence without replaying bootstrap DDL.

Reset accepts an optional UUID: `scripts/reset.ps1 -RunId <uuid>`. Fixture IDs are deterministically derived under that run, so repeatable inputs produce repeatable records and different runs do not collide.

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
