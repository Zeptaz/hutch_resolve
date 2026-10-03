"""Allow an agent to reconcile a stalled turn without replaying it."""

from alembic import op

revision = "0011_turn_recovery"
down_revision = "0010_offer_readonly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resolve.turn_claims ADD COLUMN abandoned_at timestamptz")
    op.execute("ALTER TABLE resolve.turn_claims ADD COLUMN abandoned_by_session_id uuid")
    op.execute("ALTER TABLE resolve.turn_claims ADD COLUMN abandonment_note text")
    op.execute("""
        ALTER TABLE resolve.turn_claims
        ADD CONSTRAINT turn_claims_abandonment_fields_ck CHECK (
          (abandoned_at IS NULL AND abandoned_by_session_id IS NULL AND abandonment_note IS NULL)
          OR (abandoned_at IS NOT NULL AND abandoned_by_session_id IS NOT NULL
              AND length(trim(abandonment_note)) BETWEEN 1 AND 1000)
        )
    """)
    op.execute("ALTER TABLE resolve.turn_claims ADD CONSTRAINT turn_claims_completed_or_abandoned_ck CHECK (completed_at IS NULL OR abandoned_at IS NULL)")
    op.execute("ALTER TABLE resolve.turn_claims ADD CONSTRAINT turn_claims_abandoned_by_fk FOREIGN KEY (sandbox_id,abandoned_by_session_id) REFERENCES resolve.sessions(sandbox_id,id)")
    op.execute("DROP INDEX resolve.turn_claims_active_conversation")
    op.execute("CREATE INDEX turn_claims_active_conversation ON resolve.turn_claims(conversation_id,lease_until) WHERE completed_at IS NULL AND abandoned_at IS NULL")


def downgrade() -> None:
    raise RuntimeError("Turn reconciliation history cannot be downgraded safely")
