"""Adopt the existing SQL bootstrap as the Alembic baseline.

This revision validates a complete existing schema and never drops, recreates,
or edits its tables or data. New schema changes belong in later revisions.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0001_sandbox_baseline"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED_TABLES = {
    "sandbox": {
        "accounts",
        "balance_snapshots",
        "customers",
        "fault_profiles",
        "incidents",
        "money_entries",
        "offers",
        "provider_operations",
        "quota_buckets",
        "quota_entries",
        "quota_snapshots",
        "recharges",
        "sandbox_runs",
        "service_checks",
        "subscription_events",
        "subscriptions",
        "tickets",
        "usage_records",
    },
    "resolve": {
        "action_proposals",
        "audit_events",
        "cases",
        "conversations",
        "integration_events",
        "investigations",
        "knowledge_articles",
        "messages",
        "model_calls",
        "operations",
        "receipts",
        "sessions",
        "voice_bindings",
    },
}
EXPECTED_COLUMNS = {
    "sandbox": {
        "sandbox_runs": {"id", "fixture_version", "simulation_clock", "created_at", "expires_at"},
        "customers": {"id", "sandbox_id", "display_name", "preferred_language"},
        "accounts": {"id", "sandbox_id", "customer_id", "line_alias", "account_type", "status", "region_code", "version"},
        "balance_snapshots": {"id", "sandbox_id", "account_id", "wallet_kind", "amount_minor", "currency", "as_of", "last_posting_seq"},
        "money_entries": {"id", "sandbox_id", "account_id", "wallet_kind", "posting_seq", "amount_minor", "currency", "kind", "occurred_at", "posted_at", "reversal_of"},
        "recharges": {"id", "sandbox_id", "account_id", "payment_ref", "amount_minor", "payment_status", "fulfilment_status", "credited_entry_id"},
        "offers": {"id", "sandbox_id", "offer_kind", "price_minor", "quota_bytes", "recurring", "version"},
        "subscriptions": {"id", "sandbox_id", "account_id", "offer_id", "status", "renew_enabled", "activation_evidence_ref", "version"},
        "subscription_events": {"id", "sandbox_id", "subscription_id", "event_type", "charge_entry_id"},
        "quota_buckets": {"id", "sandbox_id", "account_id", "subscription_id", "bucket_kind"},
        "quota_snapshots": {"id", "sandbox_id", "bucket_id", "remaining_bytes", "last_quota_seq"},
        "quota_entries": {"id", "sandbox_id", "bucket_id", "sequence", "delta_bytes", "entry_kind", "usage_record_id", "reversal_of"},
        "usage_records": {"id", "sandbox_id", "account_id", "bucket_id", "bytes", "usage_kind", "category"},
        "service_checks": {"id", "sandbox_id", "account_id", "check_type", "result", "observed_at", "expires_at"},
        "incidents": {"id", "sandbox_id", "region_code", "service", "status", "starts_at", "updated_at", "eta"},
        "tickets": {"id", "sandbox_id", "account_id", "case_ref", "category", "queue", "status", "packet", "version"},
        "provider_operations": {"id", "sandbox_id", "provider", "idempotency_key", "request_hash", "target_id", "expected_version", "status", "result"},
        "fault_profiles": {"id", "sandbox_id", "provider", "operation", "selector", "fault_type", "parameters", "remaining_uses"},
    },
    "resolve": {
        "sessions": {"id", "credential_hash", "role", "account_id", "sandbox_id", "expires_at", "revoked_at"},
        "conversations": {"id", "sandbox_id", "session_id", "version", "active_case_id", "expires_at"},
        "messages": {"id", "conversation_id", "client_turn_id", "speaker", "body", "result"},
        "cases": {"id", "sandbox_id", "conversation_id", "account_id", "complaint_type", "status", "version"},
        "investigations": {"id", "case_id", "revision", "evidence_state", "finding", "evidence"},
        "action_proposals": {"id", "case_id", "investigation_id", "action_type", "target_id", "proposal_hash", "expires_at"},
        "operations": {"id", "case_id", "proposal_id", "idempotency_key", "status", "confirmation", "outcome"},
        "receipts": {"id", "case_id", "revision", "receipt", "digest_sha256"},
        "voice_bindings": {"id", "sandbox_id", "conversation_id", "voice_session_id", "account_id", "origin", "expires_at"},
        "integration_events": {"id", "provider", "event_id", "request_hash", "response"},
        "audit_events": {"id", "session_id", "case_id", "event_type", "details"},
        "model_calls": {"id", "conversation_id", "provider", "model", "outcome"},
        "knowledge_articles": {"id", "article_key", "language", "title", "content", "aliases", "source_url", "reviewed_at", "version", "scope"},
    },
}
EXPECTED_SCOPED_FOREIGN_KEYS = {
    ("resolve", "sessions", "sessions_sandbox_account_fk"),
    ("resolve", "conversations", "conversations_scoped_session_fk"),
    ("resolve", "cases", "cases_sandbox_account_fk"),
    ("resolve", "cases", "cases_scoped_conversation_fk"),
    ("resolve", "conversations", "conversation_scoped_active_case_fk"),
    ("resolve", "action_proposals", "proposals_case_investigation_fk"),
    ("resolve", "operations", "operations_case_proposal_fk"),
    ("resolve", "voice_bindings", "voice_binding_scoped_conversation_fk"),
    ("resolve", "voice_bindings", "voice_binding_scoped_account_fk"),
}


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    for schema, expected in EXPECTED_TABLES.items():
        actual = set(inspector.get_table_names(schema=schema))
        missing = expected - actual
        if missing:
            raise RuntimeError(
                f"Cannot adopt incomplete {schema} schema; missing tables: "
                + ", ".join(sorted(missing))
            )
        for table, required_columns in EXPECTED_COLUMNS[schema].items():
            present = {
                column["name"] for column in inspector.get_columns(table, schema=schema)
            }
            missing_columns = required_columns - present
            if missing_columns:
                raise RuntimeError(
                    f"Cannot adopt incomplete {schema}.{table}; missing columns: "
                    + ", ".join(sorted(missing_columns))
                )

    foreign_keys = {
        (schema, table, constraint["name"])
        for schema, table, _ in EXPECTED_SCOPED_FOREIGN_KEYS
        for constraint in inspector.get_foreign_keys(table, schema=schema)
    }
    missing_scoped_keys = EXPECTED_SCOPED_FOREIGN_KEYS - foreign_keys
    if missing_scoped_keys:
        raise RuntimeError(
            "Cannot adopt incomplete sandbox-scope constraints: "
            + ", ".join(".".join(item) for item in sorted(missing_scoped_keys))
        )

    role = connection.execute(
        text("SELECT rolname FROM pg_roles WHERE rolname = :role"),
        {"role": "hutch_resolve_app"},
    ).scalar_one_or_none()
    if role is None:
        raise RuntimeError("Cannot adopt baseline: PostgreSQL role hutch_resolve_app is missing")

    connection.execute(text('GRANT SELECT ON resolve.alembic_version TO "hutch_resolve_app"'))


def downgrade() -> None:
    # The baseline predates Alembic. Removing its marker must never drop mock data.
    pass
