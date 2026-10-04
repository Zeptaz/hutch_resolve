#!/bin/sh
# Run every opt-in PostgreSQL test file, each on its own freshly reset disposable database.
#
# The default `pytest` run skips all of these, so a green default suite says nothing about SQL,
# migrations or wiring. The files consume fixture state (active VAS, one-shot faults) and some
# expect derived fixture runs, so they are not safe to run twice against one database.
#
#   sh scripts/run_db_tests.sh                    # all opt-in files
#   sh scripts/run_db_tests.sh tests/test_x.py    # selected files
#
# Uses scripts/dev_db.sh on its OWN container and port (hutch-test-db on 127.0.0.1:55435 by default),
# never the app's database: a running app's worker would otherwise pick up test operations (and, with
# CRM_PROVIDER=hubspot, send their review tickets to HubSpot). Override with TEST_DB_NAME / TEST_DB_PORT.
set -u
PY="${PYTHON:-python}"
PORT="${TEST_DB_PORT:-55435}"
NAME="${TEST_DB_NAME:-hutch-test-db}"
export DEV_DB_PORT="$PORT" DEV_DB_NAME="$NAME"
HOST="127.0.0.1:$PORT/hutch_resolve"
R="postgresql+psycopg://hutch_resolve_app:resolve-local-change-me@$HOST"
S="postgresql+psycopg://hutch_sandbox:sandbox-local-change-me@$HOST"
A="postgresql+psycopg://hutch_admin:local-only-change-me@$HOST"
BASE_RUN=00000000-0000-0000-0000-000000000001
# Test defaults: OP_IT/QUOTA_IT/PACKAGE_IT use 1111..., REVIEW_SYNC uses 3333...
DERIVED_RUNS="11111111-1111-4111-8111-111111111111 33333333-3333-4333-8333-333333333333"

if [ "$#" -eq 0 ]; then
  set -- $(grep -rl -E "IT_DATABASE_URL|IT_SANDBOX_DATABASE_URL|REVIEW_SYNC_DATABASE_URL|TURN_RECOVERY_DATABASE_URL|RESOLVE_INTEGRATION_DATABASE_URL|RESOLVE_CONVERSATION_DATABASE_URL" tests --include='*.py' | sort)
fi

failed=0
for file in "$@"; do
  if ! sh scripts/dev_db.sh reset >/dev/null 2>&1; then
    echo "== $file: database reset failed"; failed=1; continue
  fi
  SEED=$(mktemp "${TMPDIR:-/tmp}/hutch-seed.XXXXXX")
  for run in $DERIVED_RUNS; do
    "$PY" scripts/seed_run.py "$run" "$SEED" >/dev/null
    docker cp "$SEED" "$NAME:/tmp/seed-run.sql" >/dev/null
    docker exec "$NAME" psql -q -v ON_ERROR_STOP=1 -U hutch_admin -d hutch_resolve -f /tmp/seed-run.sql >/dev/null
  done
  rm -f "$SEED"
  out=$(OP_IT_DATABASE_URL=$R OP_IT_SANDBOX_DATABASE_URL=$S \
    QUOTA_IT_DATABASE_URL=$R QUOTA_IT_SANDBOX_DATABASE_URL=$S \
    PACKAGE_IT_DATABASE_URL=$R PACKAGE_IT_SANDBOX_DATABASE_URL=$S \
    REVIEW_SYNC_DATABASE_URL=$R REVIEW_SYNC_SANDBOX_DATABASE_URL=$S \
    DOMAIN_IT_DATABASE_URL=$R DOMAIN_IT_RUN_ID=$BASE_RUN \
    HUBSPOT_IT_SANDBOX_DATABASE_URL=$S HUBSPOT_IT_RUN_ID=$BASE_RUN \
    VOICE_IT_DATABASE_URL=$R RESET_IT_DATABASE_URL=$A TURN_RECOVERY_DATABASE_URL=$R \
    RESOLVE_INTEGRATION_DATABASE_URL=$R RESOLVE_INTEGRATION_SANDBOX_URL=$S \
    RESOLVE_CONVERSATION_DATABASE_URL=$R \
    "$PY" -m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning --tb=line "$file" 2>&1)
  status=$?
  echo "== $file: $(echo "$out" | tail -1)"
  if [ "$status" -ne 0 ]; then
    failed=1
    echo "$out" | grep -E "^(FAILED|ERROR) |^E " | head -6
  fi
done
sh scripts/dev_db.sh stop >/dev/null 2>&1
exit $failed
