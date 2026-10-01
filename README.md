# HUTCH Resolve — sandbox only

This repository currently prepares the synthetic telecom environment for the HUTCH Resolve hackathon entry. It intentionally contains no Resolve web app, API implementation, diagnosis logic, action executor, or customer UI.

## Start and inspect

1. Copy `.env.example` to `.env` and change the local development passwords if desired.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`.
3. On a fresh Docker volume, PostgreSQL 18 applies both SQL migrations and loads the initial run automatically.
4. Connect with any PostgreSQL client at `localhost:55432`, database `hutch_resolve`, user `hutch_admin`. The sandbox account is `hutch_sandbox`; the future Resolve account is `hutch_resolve_app`.
5. Run `powershell -ExecutionPolicy Bypass -File scripts/reset.ps1` to add another isolated fixture run. Previous runs remain available. `scripts/stop.ps1` stops the database without removing its Docker volume.

Reset accepts an optional UUID: `scripts/reset.ps1 -RunId <uuid>`. Fixture IDs are deterministically derived under that run, so repeatable inputs produce repeatable records and different runs do not collide.

The database volume is Docker-managed, outside the OneDrive-synced repository. `docker compose down -v` permanently removes local database history.

## Repository map

- `database/migrations/001_sandbox.sql`: synthetic CRM, charging, recharge, product/VAS, usage/quota and service-assurance records.
- `database/migrations/002_resolve.sql` and `003_scope_constraints.sql`: planned Resolve persistence and cross-run ownership constraints only; they do not implement API behavior.
- `database/seed.sql`: six deterministic prepaid support cases and provider fault profiles.
- `scripts/`: start/stop/reset and fixture UUID generation.
- `docs/mock-environment.md`: relationships, assumptions, failure modes and future provider contracts.

## Boundaries and security

All customer names, line aliases, transactions, offers, usage and incidents are synthetic. No real telephone numbers, HUTCH credentials, production system access, or actual vendor-specific schema is represented. `.env` is ignored by Git. The published Compose port is local development infrastructure; do not expose it to a network.

The reviewed master architecture is `HUTCH_Resolve_System_Architecture_Reviewed.pdf` in the competition workspace. HUTCH integration access is unavailable for the hackathon; provider contracts in the docs describe a future application implementation, not deployed services here.
