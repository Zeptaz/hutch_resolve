"""Add synthetic one-shot package offers and activation action support."""

from alembic import op

revision = "0009_package_activation"
down_revision = "0008_audit_remediation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only explicitly seeded one-shot synthetic data offers can be purchased.
    op.execute("ALTER TABLE sandbox.offers ADD COLUMN available_for_purchase boolean NOT NULL DEFAULT false")
    op.execute("""
        UPDATE sandbox.offers SET available_for_purchase=true
        WHERE sandbox_id='00000000-0000-0000-0000-000000000001'
          AND id IN (
            '30000000-0000-0000-0000-000000000005',
            '30000000-0000-0000-0000-000000000006',
            '30000000-0000-0000-0000-000000000007',
            '30000000-0000-0000-0000-000000000008'
          ) AND offer_kind='PACKAGE' AND recurring=false
    """)
    op.execute("ALTER TABLE resolve.action_proposals DROP CONSTRAINT action_proposals_action_type_check")
    op.execute("""
        ALTER TABLE resolve.action_proposals ADD CONSTRAINT action_proposals_action_type_check
        CHECK (action_type IN ('DEACTIVATE_VAS','SEND_SETTINGS_INSTRUCTIONS','CREATE_REVIEW_TICKET','ACTIVATE_PACKAGE'))
    """)
    # Existing code persists an escalation reason, and reads it when the action
    # worker creates the mock ticket. Keep it in this forward schema revision.
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN escalation_reason text")
    op.execute("""
        ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposal_escalation_reason_ck
        CHECK ((action_type='CREATE_REVIEW_TICKET') OR escalation_reason IS NULL)
    """)
    # Correct expired grant semantics: an expired encrypted grant is cleared and
    # becomes permanently non-replayable instead of violating the success check.
    op.execute("ALTER TABLE resolve.voice_grant_requests DROP CONSTRAINT voice_grant_requests_state_check")
    op.execute("""
        ALTER TABLE resolve.voice_grant_requests ADD CONSTRAINT voice_grant_requests_state_check
        CHECK (state IN ('PROVISIONING','SUCCEEDED','FAILED','UNKNOWN','EXPIRED'))
    """)
    op.execute('GRANT SELECT, UPDATE ON sandbox.offers TO "hutch_resolve_app"')


def downgrade() -> None:
    raise RuntimeError("Package activation and expired grant history are not safely reversible")
