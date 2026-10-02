"""Persist originating case turns and immutable investigation calculations."""

from __future__ import annotations

from alembic import op

revision = "0003_case_investigations"
down_revision = "0002_domain_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resolve.cases ADD COLUMN origin_turn_id uuid")
    op.execute("ALTER TABLE resolve.cases ADD COLUMN origin_request_hash text")
    op.execute("ALTER TABLE resolve.cases ADD COLUMN reported_facts jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE resolve.cases ADD CONSTRAINT cases_origin_pair_ck CHECK ((origin_turn_id IS NULL) = (origin_request_hash IS NULL))")
    op.execute("CREATE UNIQUE INDEX cases_conversation_origin_turn_uq ON resolve.cases(conversation_id,origin_turn_id) WHERE origin_turn_id IS NOT NULL")

    op.execute("ALTER TABLE resolve.investigations ADD COLUMN calculations jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN source_status jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN missing text[] NOT NULL DEFAULT '{}'::text[]")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN conflicts text[] NOT NULL DEFAULT '{}'::text[]")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN eligible_actions jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN review_reasons text[] NOT NULL DEFAULT '{}'::text[]")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN window_start timestamptz")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN window_end timestamptz")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN command_key text")
    op.execute("ALTER TABLE resolve.investigations ADD COLUMN request_hash text")
    op.execute("ALTER TABLE resolve.investigations ADD CONSTRAINT investigations_window_pair_ck CHECK ((window_start IS NULL) = (window_end IS NULL))")
    op.execute("ALTER TABLE resolve.investigations ADD CONSTRAINT investigations_command_pair_ck CHECK ((command_key IS NULL) = (request_hash IS NULL))")
    op.execute("CREATE UNIQUE INDEX investigations_case_command_uq ON resolve.investigations(case_id,command_key) WHERE command_key IS NOT NULL")

    op.execute('REVOKE UPDATE, DELETE ON resolve.investigations FROM "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT ON resolve.investigations TO "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT ON resolve.cases TO "hutch_resolve_app"')
    op.execute('GRANT UPDATE ON resolve.cases, resolve.conversations TO "hutch_resolve_app"')


def downgrade() -> None:
    # Investigation and case history are retained; destructive rollback is not offered.
    raise RuntimeError("Revision 0003 is append-only and cannot be downgraded safely")
