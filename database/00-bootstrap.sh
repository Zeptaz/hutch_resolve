#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=sandbox_user="$SANDBOX_DB_USER" --set=sandbox_password="$SANDBOX_DB_PASSWORD" \
  --set=resolve_user="$RESOLVE_DB_USER" --set=resolve_password="$RESOLVE_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'sandbox_user', :'sandbox_password') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'sandbox_user') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'resolve_user', :'resolve_password') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'resolve_user') \gexec
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS sandbox;
CREATE SCHEMA IF NOT EXISTS resolve;
SELECT format('GRANT USAGE ON SCHEMA sandbox TO %I', :'sandbox_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA sandbox TO %I', :'sandbox_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA sandbox GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', :'sandbox_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA resolve TO %I', :'resolve_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA resolve TO %I', :'resolve_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA resolve GRANT SELECT, INSERT ON TABLES TO %I', :'resolve_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA sandbox TO %I', :'resolve_user') \gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA sandbox TO %I', :'resolve_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA sandbox GRANT SELECT ON TABLES TO %I', :'resolve_user') \gexec
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrations/001_sandbox.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrations/002_resolve.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrations/003_scope_constraints.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrations/004_rated_events.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/knowledge_seed.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=resolve_user="$RESOLVE_DB_USER" <<'SQL'
SELECT format('GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA resolve TO %I', :'resolve_user') \gexec
SELECT format('GRANT UPDATE ON resolve.sessions, resolve.conversations, resolve.cases, resolve.action_proposals, resolve.operations, resolve.voice_bindings TO %I', :'resolve_user') \gexec
SELECT format('REVOKE UPDATE ON resolve.receipts, resolve.audit_events, resolve.integration_events, resolve.model_calls, resolve.knowledge_articles FROM %I', :'resolve_user') \gexec
SQL
