"""Add the human-review reason that proposals and the action runner already use.

Stopgap from branch tevin/hubspot-crm so a fresh database matches ResolveDev code
(02d986b/aa6af0e read and write action_proposals.escalation_reason). Harry owns
migrations: replace or renumber this if ResolveDev adds its own revision.
"""

from alembic import op

revision = "0009_escalation_reason"
down_revision = "0008_audit_remediation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN IF NOT EXISTS escalation_reason text")
    op.execute("""
        DO $$ BEGIN
          ALTER TABLE resolve.action_proposals ADD CONSTRAINT action_proposals_escalation_reason_len
            CHECK (escalation_reason IS NULL OR char_length(escalation_reason) BETWEEN 1 AND 2000);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE resolve.action_proposals DROP CONSTRAINT IF EXISTS action_proposals_escalation_reason_len")
    op.execute("ALTER TABLE resolve.action_proposals DROP COLUMN IF EXISTS escalation_reason")
