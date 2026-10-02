#!/bin/sh
# Throwaway migrated PostgreSQL for the dev backend's hybrid mode (port 55433).
# Never the shared hutch-resolve-postgres-1 container. Run from the repo root:
#   sh tests/conversation/hybrid_db.sh start   # create, migrate, print the env for hybrid mode
#   sh tests/conversation/hybrid_db.sh stop    # delete it (all hybrid test data is discarded)
set -eu
PY="${PYTHON:-python}"
NAME=hutch-tevin-hybrid
PORT=55433

case "${1:-}" in
  start)
    [ -x database/00-bootstrap.sh ] || { echo "chmod +x database/00-bootstrap.sh first (see docs/plans/tevin.md)"; exit 1; }
    docker rm -f "$NAME" >/dev/null 2>&1 || true
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
      "$PY" -m alembic upgrade head >/dev/null 2>&1
    echo "Hybrid database ready on 127.0.0.1:$PORT. Start the dev backend with:"
    echo "  RESOLVE_BACKEND=hybrid \\"
    echo "  RESOLVE_DEV_DATABASE_URL=postgresql+psycopg://hutch_resolve_app:resolve-local-change-me@127.0.0.1:$PORT/hutch_resolve \\"
    echo "  RESOLVE_DEV_SANDBOX_URL=postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@127.0.0.1:$PORT/hutch_resolve \\"
    echo "  $PY tests/conversation/dev_backend.py"
    ;;
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Hybrid database removed." || echo "Nothing to remove."
    ;;
  *)
    echo "usage: sh tests/conversation/hybrid_db.sh start|stop"; exit 2
    ;;
esac
