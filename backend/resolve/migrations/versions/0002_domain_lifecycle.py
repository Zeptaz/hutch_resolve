"""Add run lifecycle, turn claims, confirmations, and review history."""

from __future__ import annotations

from alembic import op

revision = "0002_domain_lifecycle"
down_revision = "0001_sandbox_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sandbox.sandbox_runs ADD COLUMN run_status text NOT NULL DEFAULT 'ACTIVE' CHECK (run_status IN ('ACTIVE','RETIRED'))")
    op.execute("ALTER TABLE sandbox.sandbox_runs ADD COLUMN retired_at timestamptz")
    op.execute("ALTER TABLE sandbox.sandbox_runs ADD CONSTRAINT sandbox_retired_time_ck CHECK ((run_status='ACTIVE' AND retired_at IS NULL) OR (run_status='RETIRED' AND retired_at IS NOT NULL))")

    op.execute("ALTER TABLE resolve.sessions ADD COLUMN principal_id text NOT NULL DEFAULT 'legacy-demo'")
    op.execute("ALTER TABLE resolve.sessions ADD COLUMN csrf_hash bytea")
    op.execute("ALTER TABLE resolve.sessions ADD COLUMN channel text NOT NULL DEFAULT 'TEXT' CHECK (channel IN ('TEXT','VOICE','AGENT'))")
    op.execute("ALTER TABLE resolve.sessions DROP CONSTRAINT sessions_role_check")
    op.execute("ALTER TABLE resolve.sessions ADD CONSTRAINT sessions_role_check CHECK (role IN ('GUEST','CUSTOMER','AGENT','SIMULATOR'))")
    op.execute("CREATE INDEX sessions_scope_expiry ON resolve.sessions(sandbox_id,account_id,expires_at) WHERE revoked_at IS NULL")

    op.execute("ALTER TABLE resolve.conversations ADD COLUMN language text NOT NULL DEFAULT 'en' CHECK (language IN ('en','si','ta'))")
    op.execute("ALTER TABLE resolve.conversations ADD COLUMN pending_question jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE resolve.messages ADD COLUMN input_hash text")
    op.execute("ALTER TABLE resolve.messages ADD COLUMN accepted_result jsonb")
    op.execute("""
        CREATE TABLE resolve.turn_claims (
          sandbox_id uuid NOT NULL,
          conversation_id uuid NOT NULL,
          client_turn_id uuid NOT NULL,
          input_hash text NOT NULL,
          downstream_key uuid NOT NULL,
          claimed_at timestamptz NOT NULL DEFAULT now(),
          lease_until timestamptz NOT NULL,
          completed_at timestamptz,
          result jsonb,
          PRIMARY KEY (conversation_id, client_turn_id),
          FOREIGN KEY (sandbox_id, conversation_id) REFERENCES resolve.conversations(sandbox_id,id),
          CHECK ((completed_at IS NULL AND result IS NULL) OR (completed_at IS NOT NULL AND result IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX turn_claims_recovery ON resolve.turn_claims(lease_until) WHERE completed_at IS NULL")

    op.execute("ALTER TABLE resolve.cases ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE resolve.cases ADD COLUMN review_status text NOT NULL DEFAULT 'NEW' CHECK (review_status IN ('NEW','IN_REVIEW','CLOSED'))")
    op.execute("ALTER TABLE resolve.cases ADD COLUMN review_version integer NOT NULL DEFAULT 1 CHECK (review_version > 0)")
    op.execute("CREATE INDEX cases_review_queue ON resolve.cases(sandbox_id,review_status,updated_at DESC,id)")

    op.execute("""
        CREATE TABLE resolve.confirmations (
          id uuid PRIMARY KEY,
          sandbox_id uuid NOT NULL,
          case_id uuid NOT NULL,
          proposal_id uuid NOT NULL,
          proposal_hash text NOT NULL,
          actor_session_id uuid NOT NULL,
          source_channel text NOT NULL CHECK (source_channel IN ('TEXT','VOICE','AGENT')),
          client_turn_id uuid,
          decision text NOT NULL CHECK (decision IN ('ACCEPT','DECLINE')),
          recorded_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (sandbox_id,case_id) REFERENCES resolve.cases(sandbox_id,id),
          FOREIGN KEY (case_id,proposal_id) REFERENCES resolve.action_proposals(case_id,id),
          FOREIGN KEY (sandbox_id,actor_session_id) REFERENCES resolve.sessions(sandbox_id,id),
          UNIQUE (sandbox_id,id),
          UNIQUE (case_id,id)
        )
    """)
    op.execute("ALTER TABLE resolve.operations ADD COLUMN confirmation_id uuid")
    op.execute("ALTER TABLE resolve.operations ADD COLUMN request_fingerprint text")
    op.execute("ALTER TABLE resolve.operations ADD COLUMN recovery_after timestamptz")
    op.execute("ALTER TABLE resolve.operations ADD COLUMN provider_operation_ref text")
    op.execute("ALTER TABLE resolve.operations ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)")
    op.execute("ALTER TABLE resolve.operations ADD CONSTRAINT operations_id_case_uq UNIQUE (id,case_id)")
    op.execute("ALTER TABLE resolve.operations ADD CONSTRAINT operations_confirmation_fk FOREIGN KEY (case_id,confirmation_id) REFERENCES resolve.confirmations(case_id,id)")
    op.execute("CREATE UNIQUE INDEX operations_one_per_proposal ON resolve.operations(case_id,proposal_id)")

    op.execute("""
        CREATE TABLE resolve.review_events (
          id uuid PRIMARY KEY,
          sandbox_id uuid NOT NULL,
          case_id uuid NOT NULL,
          actor_session_id uuid NOT NULL,
          case_version integer NOT NULL CHECK (case_version > 0),
          review_status text NOT NULL CHECK (review_status IN ('NEW','IN_REVIEW','CLOSED')),
          disposition text,
          note text NOT NULL CHECK (length(note) <= 2000),
          visibility text NOT NULL DEFAULT 'INTERNAL' CHECK (visibility='INTERNAL'),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (sandbox_id,case_id) REFERENCES resolve.cases(sandbox_id,id),
          FOREIGN KEY (sandbox_id,actor_session_id) REFERENCES resolve.sessions(sandbox_id,id)
        )
    """)
    op.execute("CREATE INDEX review_events_case_time ON resolve.review_events(sandbox_id,case_id,created_at,id)")
    op.execute("""
        CREATE TABLE resolve.escalation_deliveries (
          id uuid PRIMARY KEY,
          sandbox_id uuid NOT NULL,
          case_id uuid NOT NULL,
          operation_id uuid,
          delivery_state text NOT NULL CHECK (delivery_state IN ('PENDING','DELIVERED','FAILED','REVIEW_REQUIRED')),
          provider_ticket_id text,
          request_key text NOT NULL UNIQUE,
          last_error_code text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (sandbox_id,case_id) REFERENCES resolve.cases(sandbox_id,id),
          FOREIGN KEY (operation_id,case_id) REFERENCES resolve.operations(id,case_id),
          CHECK ((delivery_state='DELIVERED' AND provider_ticket_id IS NOT NULL) OR delivery_state <> 'DELIVERED')
        )
    """)
    op.execute("""
        CREATE TABLE resolve.idempotency_records (
          id uuid PRIMARY KEY,
          subject_id text NOT NULL,
          route_key text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL,
          response_status integer,
          response_body jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          UNIQUE(subject_id,route_key,idempotency_key),
          CHECK ((completed_at IS NULL AND response_status IS NULL AND response_body IS NULL) OR
                 (completed_at IS NOT NULL AND response_status IS NOT NULL AND response_body IS NOT NULL))
        )
    """)

    # Runtime may append audit/confirmation/review records but cannot rewrite history.
    op.execute('REVOKE UPDATE, DELETE ON resolve.confirmations, resolve.review_events, resolve.audit_events, resolve.receipts, resolve.integration_events, resolve.model_calls, resolve.idempotency_records FROM "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT, UPDATE ON resolve.turn_claims, resolve.escalation_deliveries, resolve.idempotency_records TO "hutch_resolve_app"')
    op.execute('GRANT SELECT, INSERT ON resolve.confirmations, resolve.review_events TO "hutch_resolve_app"')
    op.execute('GRANT UPDATE ON resolve.sessions, resolve.conversations, resolve.cases, resolve.action_proposals, resolve.operations, resolve.voice_bindings TO "hutch_resolve_app"')


def downgrade() -> None:
    # Preserve runtime records. Downgrade only removes this revision's structures.
    op.execute('REVOKE ALL ON resolve.turn_claims, resolve.confirmations, resolve.review_events, resolve.escalation_deliveries, resolve.idempotency_records FROM "hutch_resolve_app"')
    op.execute("DROP TABLE resolve.idempotency_records")
    op.execute("DROP TABLE resolve.escalation_deliveries")
    op.execute("DROP TABLE resolve.review_events")
    op.execute("ALTER TABLE resolve.operations DROP CONSTRAINT operations_confirmation_fk")
    op.execute("ALTER TABLE resolve.operations DROP CONSTRAINT operations_id_case_uq")
    op.execute("DROP INDEX resolve.operations_one_per_proposal")
    op.execute("ALTER TABLE resolve.operations DROP COLUMN confirmation_id, DROP COLUMN request_fingerprint, DROP COLUMN recovery_after, DROP COLUMN provider_operation_ref, DROP COLUMN attempt_count")
    op.execute("DROP TABLE resolve.confirmations")
    op.execute("DROP INDEX resolve.cases_review_queue")
    op.execute("ALTER TABLE resolve.cases DROP COLUMN updated_at, DROP COLUMN review_status, DROP COLUMN review_version")
    op.execute("DROP INDEX resolve.turn_claims_recovery")
    op.execute("DROP TABLE resolve.turn_claims")
    op.execute("ALTER TABLE resolve.messages DROP COLUMN input_hash, DROP COLUMN accepted_result")
    op.execute("ALTER TABLE resolve.conversations DROP COLUMN language, DROP COLUMN pending_question")
    op.execute("DROP INDEX resolve.sessions_scope_expiry")
    op.execute("ALTER TABLE resolve.sessions DROP CONSTRAINT sessions_role_check")
    op.execute("ALTER TABLE resolve.sessions ADD CONSTRAINT sessions_role_check CHECK (role IN ('CUSTOMER','AGENT','SIMULATOR'))")
    op.execute("ALTER TABLE resolve.sessions DROP COLUMN principal_id, DROP COLUMN csrf_hash, DROP COLUMN channel")
    op.execute("ALTER TABLE sandbox.sandbox_runs DROP CONSTRAINT sandbox_retired_time_ck")
    op.execute("ALTER TABLE sandbox.sandbox_runs DROP COLUMN run_status, DROP COLUMN retired_at")
