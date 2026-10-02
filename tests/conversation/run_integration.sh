#!/bin/sh
# Conversation module against Harry's real ResolveFacade on a THROWAWAY PostgreSQL.
# Never touches the shared hutch-resolve-postgres-1 container: uses its own container on port 55433
# and removes it afterwards. Run from the repo root:  sh tests/conversation/run_integration.sh
set -eu
PY="${PYTHON:-python}"
NAME=hutch-tevin-integration
PORT=55433
docker rm -f "$NAME" >/dev/null 2>&1 || true
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

# database/00-bootstrap.sh must be executable for the postgres entrypoint (it is 100644 in Git).
[ -x database/00-bootstrap.sh ] || { echo "chmod +x database/00-bootstrap.sh first (see docs/plans/tevin.md)"; exit 1; }

docker run -d --name "$NAME" -p "127.0.0.1:$PORT:5432" \
  -e POSTGRES_DB=hutch_resolve -e POSTGRES_USER=hutch_admin -e POSTGRES_PASSWORD=local-only-change-me \
  -e SANDBOX_DB_USER=hutch_sandbox -e SANDBOX_DB_PASSWORD=sandbox-local-change-me \
  -e RESOLVE_DB_USER=hutch_resolve_app -e RESOLVE_DB_PASSWORD=resolve-local-change-me \
  -v "$PWD/database:/docker-entrypoint-initdb.d:ro" postgres:18-alpine >/dev/null
i=0
until docker exec "$NAME" sh -c 'test -f "$PGDATA/.hutch_initialized"' 2>/dev/null; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { docker logs "$NAME" | tail -20; exit 1; }; sleep 1
done

MIGRATION_DATABASE_URL="postgresql+psycopg://hutch_admin:local-only-change-me@127.0.0.1:$PORT/hutch_resolve" \
  "$PY" -m alembic upgrade head >/dev/null
RESOLVE_INTEGRATION_DATABASE_URL="postgresql+psycopg://hutch_resolve_app:resolve-local-change-me@127.0.0.1:$PORT/hutch_resolve" \
RESOLVE_INTEGRATION_SANDBOX_URL="postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@127.0.0.1:$PORT/hutch_resolve" \
  "$PY" -m pytest tests/conversation/test_resolve_integration.py -q -p no:cacheprovider -W ignore::DeprecationWarning
