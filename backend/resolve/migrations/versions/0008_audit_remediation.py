"""Persist authentication throttles and idempotent Voice grants."""

from alembic import op

revision = "0008_audit_remediation"
down_revision = "0007_conversation_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE resolve.auth_rate_limits (
          bucket_hash bytea PRIMARY KEY,
          window_started_at timestamptz NOT NULL,
          attempt_count integer NOT NULL CHECK (attempt_count >= 0),
          expires_at timestamptz NOT NULL
        )
    """)
    op.execute("CREATE INDEX auth_rate_limits_expiry_idx ON resolve.auth_rate_limits(expires_at)")
    op.execute("""
        CREATE TABLE resolve.voice_grant_requests (
          id uuid PRIMARY KEY,
          sandbox_id uuid NOT NULL,
          session_id uuid NOT NULL,
          conversation_id uuid NOT NULL,
          idempotency_key uuid NOT NULL,
          request_fingerprint text NOT NULL,
          binding_id uuid NOT NULL UNIQUE,
          voice_session_id uuid NOT NULL UNIQUE,
          request_body jsonb NOT NULL,
          outbound_event_id uuid NOT NULL UNIQUE,
          state text NOT NULL CHECK (state IN ('PROVISIONING','SUCCEEDED','FAILED','UNKNOWN')),
          encrypted_grant bytea,
          grant_expires_at timestamptz,
          error_code text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (session_id,idempotency_key),
          FOREIGN KEY (sandbox_id,session_id) REFERENCES resolve.sessions(sandbox_id,id),
          FOREIGN KEY (sandbox_id,conversation_id) REFERENCES resolve.conversations(sandbox_id,id),
          CHECK ((state='SUCCEEDED') = (encrypted_grant IS NOT NULL)),
          CHECK ((state='SUCCEEDED') = (grant_expires_at IS NOT NULL))
        )
    """)
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON resolve.auth_rate_limits TO "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT, UPDATE ON resolve.voice_grant_requests TO "hutch_resolve_app"')


def downgrade() -> None:
    raise RuntimeError("Audit remediation state contains replay/security history and is not safely reversible")
