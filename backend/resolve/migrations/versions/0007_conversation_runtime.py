"""Persist dialogue state and conversation model-call audit metadata."""

from alembic import op

revision = "0007_conversation_runtime"
down_revision = "0006_audit_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A guest session has no sandbox. Its conversation may answer public FAQs,
    # but business facade operations remain customer-only.
    op.execute("ALTER TABLE resolve.turn_claims ALTER COLUMN sandbox_id DROP NOT NULL")
    op.execute("ALTER TABLE resolve.conversations ADD COLUMN dialogue_state jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE resolve.messages ADD COLUMN source_reply_text text")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN request_id uuid")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN case_id uuid")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN purpose text")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN prompt_version text")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN attempt integer")
    op.execute("ALTER TABLE resolve.model_calls ADD COLUMN error_type text")


def downgrade() -> None:
    raise RuntimeError("Conversation audit history cannot be downgraded safely")
