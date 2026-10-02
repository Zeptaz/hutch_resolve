# HUTCH Resolve — sandbox only

This repository prepares the synthetic telecom environment and the first Resolve backend foundation for the HUTCH Resolve hackathon entry. Business APIs, authorization, diagnosis, actions and customer/dashboard UIs are still under development.

## Team implementation plan

Start with [context.md](context.md), the agent-maintained source of truth, and the owner plans for [Harry](docs/plans/harry.md), [Jayith](docs/plans/jayith.md), and [Tevin](docs/plans/tevin.md). [Shared contracts](docs/contracts.md) describe upcoming application work; they are not deployed APIs. The plan records known fixture corrections and incomplete Voice qualification.

## Start and inspect

1. Copy `.env.example` to `.env` and change the local development passwords if desired.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`.
3. On a fresh Docker volume, PostgreSQL 18 applies both SQL migrations and loads the initial run automatically.
4. Adopt the existing SQL baseline into Alembic with `python -m alembic upgrade head` (the migration connection uses `MIGRATION_DATABASE_URL`). Connect with any PostgreSQL client at `localhost:55432`, database `hutch_resolve`, user `hutch_admin`. The sandbox account is `hutch_sandbox`; the application account is `hutch_resolve_app`.
5. Run `powershell -ExecutionPolicy Bypass -File scripts/reset.ps1` to add another isolated fixture run. Previous runs remain available. `scripts/stop.ps1` stops the database without removing its Docker volume.

## Start the backend foundation

The starter exposes only process liveness and readiness for PostgreSQL plus Alembic schema revision `0002_domain_lifecycle`; it does not yet implement Resolve business APIs.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --app-dir backend/resolve --host 127.0.0.1 --port 8080
```

The application reads `DATABASE_URL` from `.env`; it uses the separate `hutch_resolve_app` local role for PostgreSQL. `GET /api/v1/healthz` checks process liveness. `GET /api/v1/readyz` checks PostgreSQL and the adopted schema revision. Alembic uses the local admin `MIGRATION_DATABASE_URL` to create its revision table; the first revision only validates/adopts schemas 001-003 and will not recreate or alter sandbox rows.

Reset accepts an optional UUID: `scripts/reset.ps1 -RunId <uuid>`. Fixture IDs are deterministically derived under that run, so repeatable inputs produce repeatable records and different runs do not collide.

The database volume is Docker-managed, outside the OneDrive-synced repository. `docker compose down -v` permanently removes local database history.

## Repository map

- `database/migrations/001_sandbox.sql`: synthetic CRM, charging, recharge, product/VAS, usage/quota and service-assurance records.
- `database/migrations/002_resolve.sql` and `003_scope_constraints.sql`: planned Resolve persistence and cross-run ownership constraints only; they do not implement API behavior.
- `database/seed.sql`: fixture version 2 with six deterministic prepaid support cases and provider fault profiles.
- `backend/resolve/app/`: FastAPI startup and health/readiness foundation.
- `backend/resolve/migrations/`: Alembic migration environment, non-destructive legacy baseline adoption and the first additive domain-lifecycle migration.
- `scripts/`: start/stop/reset and fixture UUID generation.
- `docs/mock-environment.md`: relationships, assumptions, failure modes and future provider contracts.

## Boundaries and security

All customer names, line aliases, transactions, offers, usage and incidents are synthetic. No real telephone numbers, HUTCH credentials, production system access, or actual vendor-specific schema is represented. `.env` is ignored by Git. The published Compose port is local development infrastructure; do not expose it to a network.

The reviewed master architecture is `HUTCH_Resolve_System_Architecture_Reviewed.pdf` in the competition workspace. HUTCH integration access is unavailable for the hackathon; provider contracts in the docs describe a future application implementation, not deployed services here.
