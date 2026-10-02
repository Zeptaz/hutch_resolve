"""Bind proposals and confirmations to a session, evidence and client turn."""

from __future__ import annotations

from alembic import op

revision = "0004_action_proposals"
down_revision = "0003_case_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN sandbox_id uuid")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN actor_session_id uuid")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN evidence_revision integer")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN case_version integer")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN request_key text")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN request_hash text")
    op.execute("ALTER TABLE resolve.action_proposals ADD COLUMN target_label text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE resolve.confirmations ADD COLUMN request_fingerprint text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposals_sandbox_case_fk FOREIGN KEY(sandbox_id,case_id) REFERENCES resolve.cases(sandbox_id,id)")
    op.execute("ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposals_sandbox_session_fk FOREIGN KEY(sandbox_id,actor_session_id) REFERENCES resolve.sessions(sandbox_id,id)")
    op.execute("ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposals_session_required_ck CHECK ((sandbox_id IS NULL) = (actor_session_id IS NULL))")
    op.execute("ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposals_version_pair_ck CHECK ((evidence_revision IS NULL) = (case_version IS NULL))")
    op.execute("ALTER TABLE resolve.action_proposals ADD CONSTRAINT proposals_request_pair_ck CHECK ((request_key IS NULL) = (request_hash IS NULL))")
    op.execute("CREATE UNIQUE INDEX proposals_case_request_key_uq ON resolve.action_proposals(case_id,request_key) WHERE request_key IS NOT NULL")

    op.execute("CREATE UNIQUE INDEX confirmations_session_turn_uq ON resolve.confirmations(sandbox_id,actor_session_id,client_turn_id) WHERE client_turn_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX operations_confirmation_uq ON resolve.operations(confirmation_id) WHERE confirmation_id IS NOT NULL")
    op.execute('GRANT SELECT, INSERT, UPDATE ON resolve.action_proposals TO "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT, UPDATE ON resolve.operations TO "hutch_resolve_app"')


def downgrade() -> None:
    raise RuntimeError("Revision 0004 contains action audit history and cannot be downgraded safely")
