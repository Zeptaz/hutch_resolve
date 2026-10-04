"""Add itemised rated usage and the investigation outcome (explained / unexplained classification)."""

from pathlib import Path

from alembic import op

revision = "0012_investigation_outcome"
down_revision = "0011_turn_recovery"
branch_labels = None
depends_on = None

RATED_EVENTS_SQL = Path(__file__).resolve().parents[4] / "database" / "migrations" / "004_rated_events.sql"


def upgrade() -> None:
    # Fresh volumes already have the table from database/migrations/004_rated_events.sql (it must
    # exist before seed.sql loads); older databases get the same idempotent definition here.
    statements = RATED_EVENTS_SQL.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", "")
    op.execute(statements)
    op.execute('GRANT SELECT ON sandbox.rated_events TO "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON sandbox.rated_events TO "hutch_sandbox"')
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN outcome jsonb")


def downgrade() -> None:
    raise RuntimeError("Investigation outcomes are audit history and cannot be downgraded safely")
