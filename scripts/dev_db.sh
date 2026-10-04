#!/bin/sh
# Disposable PostgreSQL for local demos and tests on macOS/Linux (the PowerShell scripts cover Windows).
# Never touches the shared compose database. Run from the repository root:
#
#   sh scripts/dev_db.sh reset            # recreate, seed and migrate to head (all data discarded)
#   sh scripts/dev_db.sh reset --demo     # same, then clear seeded faults and arm only the CRM outage
#   sh scripts/dev_db.sh stop             # remove the container
#
# Defaults: container hutch-dev-db on 127.0.0.1:55434. Override with DEV_DB_NAME / DEV_DB_PORT.
# The URLs to put in .env are printed after a reset.
set -eu
PY="${PYTHON:-python}"
NAME="${DEV_DB_NAME:-hutch-dev-db}"
PORT="${DEV_DB_PORT:-55434}"
HOST="127.0.0.1:$PORT/hutch_resolve"
ADMIN="postgresql+psycopg://hutch_admin:local-only-change-me@$HOST"

case "${1:-}" in
  reset)
    # The database/*.sh init scripts are 100644 in Git; the postgres entrypoint must execute them
    # rather than source them, so mount an executable temporary copy instead of changing file modes.
    INITDB=$(mktemp -d "${TMPDIR:-/tmp}/hutch-initdb.XXXXXX")
    cp -R database/. "$INITDB/"
    chmod +x "$INITDB"/*.sh
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" -p "127.0.0.1:$PORT:5432" \
      -e POSTGRES_DB=hutch_resolve -e POSTGRES_USER=hutch_admin -e POSTGRES_PASSWORD=local-only-change-me \
      -e SANDBOX_DB_USER=hutch_sandbox -e SANDBOX_DB_PASSWORD=sandbox-local-change-me \
      -e RESOLVE_DB_USER=hutch_resolve_app -e RESOLVE_DB_PASSWORD=resolve-local-change-me \
      -v "$INITDB:/docker-entrypoint-initdb.d:ro" postgres:18-alpine >/dev/null
    i=0
    # 99-ready.sh writes the marker only after every init script has run.
    until docker exec "$NAME" sh -c 'test -f "$PGDATA/.hutch_initialized"' 2>/dev/null; do
      i=$((i + 1)); [ "$i" -gt 90 ] && { docker logs "$NAME" | tail -20; exit 1; }; sleep 1
    done
    until docker exec "$NAME" pg_isready -q -U hutch_admin -d hutch_resolve 2>/dev/null; do sleep 1; done
    MIGRATION_DATABASE_URL="$ADMIN" "$PY" -m alembic upgrade head >/dev/null 2>&1
    echo "Database ready on 127.0.0.1:$PORT at $(MIGRATION_DATABASE_URL="$ADMIN" "$PY" -m alembic current 2>/dev/null | tail -1)"
    if [ "${2:-}" = "--demo" ]; then
      SANDBOX_DATABASE_URL="postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@$HOST" "$PY" scripts/demo_faults.py clear
      SANDBOX_DATABASE_URL="postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@$HOST" "$PY" scripts/demo_faults.py arm crm-outage
    fi
    echo "DATABASE_URL=postgresql+psycopg://hutch_resolve_app:resolve-local-change-me@$HOST"
    echo "SANDBOX_DATABASE_URL=postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@$HOST"
    echo "MIGRATION_DATABASE_URL=$ADMIN"
    echo "Restart the backend: sessions and conversations from before the reset no longer exist."
    ;;
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Removed $NAME." || echo "Nothing to remove."
    ;;
  *)
    echo "usage: sh scripts/dev_db.sh reset [--demo] | stop"; exit 2
    ;;
esac
