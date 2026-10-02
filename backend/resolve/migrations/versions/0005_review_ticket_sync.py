"""Persist an idempotent outbox for agent review updates to mock CRM tickets."""

from alembic import op

revision = "0005_review_ticket_sync"
down_revision = "0004_action_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE resolve.review_sync_jobs (
          id uuid PRIMARY KEY,
          sandbox_id uuid NOT NULL,
          case_id uuid NOT NULL,
          review_event_id uuid NOT NULL UNIQUE REFERENCES resolve.review_events(id),
          provider_ticket_id text NOT NULL,
          status text NOT NULL CHECK (status IN ('PENDING','RUNNING','UNKNOWN','SYNCED','FAILED','REVIEW_REQUIRED')),
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count>=0),
          lease_until timestamptz,
          recovery_after timestamptz,
          last_error_code text,
          provider_operation_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (sandbox_id,case_id) REFERENCES resolve.cases(sandbox_id,id),
          CHECK ((status='RUNNING') = (lease_until IS NOT NULL)),
          CHECK ((status='UNKNOWN') = (recovery_after IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX review_sync_ready ON resolve.review_sync_jobs(status,recovery_after,lease_until,created_at)")
    op.execute('GRANT SELECT, INSERT, UPDATE ON resolve.review_sync_jobs TO "hutch_resolve_app"')


def downgrade() -> None:
    raise RuntimeError("Review ticket sync history is not safely reversible")
