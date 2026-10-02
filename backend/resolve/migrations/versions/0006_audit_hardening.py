"""Store Voice consent provenance and fence recovery claims."""

from __future__ import annotations

from alembic import op

revision = "0006_audit_hardening"
down_revision = "0005_review_ticket_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resolve.turn_claims ADD COLUMN claim_token uuid")
    op.execute("ALTER TABLE resolve.turn_claims ADD COLUMN input_payload jsonb")
    op.execute("ALTER TABLE resolve.idempotency_records ADD COLUMN claim_token uuid")
    op.execute("ALTER TABLE resolve.confirmations ADD COLUMN voice_binding_id uuid")
    op.execute("ALTER TABLE resolve.confirmations ADD COLUMN voice_turn_id uuid")
    op.execute("ALTER TABLE resolve.confirmations ADD COLUMN voice_transcript_sha256 text")
    op.execute("ALTER TABLE resolve.confirmations ADD COLUMN voice_presentation_response_id text")
    op.execute("""
        ALTER TABLE resolve.confirmations ADD CONSTRAINT confirmations_voice_provenance_ck
        CHECK (source_channel <> 'VOICE' OR
               (voice_binding_id IS NOT NULL AND voice_turn_id IS NOT NULL
                AND voice_transcript_sha256 IS NOT NULL
                AND voice_presentation_response_id IS NOT NULL)) NOT VALID
    """)
    op.execute("CREATE INDEX turn_claims_active_conversation ON resolve.turn_claims(conversation_id,lease_until) WHERE completed_at IS NULL")


def downgrade() -> None:
    raise RuntimeError("Voice consent and recovery history cannot be downgraded safely")
