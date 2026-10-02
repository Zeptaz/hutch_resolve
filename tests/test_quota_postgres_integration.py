"""Opt-in: QUOTA_IT_DATABASE_URL must reference a disposable DB with migrated seed.sql fixtures."""
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import create_engine

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.app.case_api import InvestigationView
from backend.resolve.providers.sandbox import PostgresSandboxProvider, reconcile_quota
from backend.resolve.services.facade import ResolveFacade


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL"), reason="requires disposable seeded PostgreSQL")
def test_seeded_data_depletion_case_reconciles_bytes_and_separate_lkr_charge():
    engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000002")
    start = datetime.fromisoformat("2026-10-02T08:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        provider = PostgresSandboxProvider(engine)
        quota_sources = provider.get_quota_statements(run_id, account_id, start, end)
        assert len(quota_sources) == 1
        quota = reconcile_quota(quota_sources[0][0], quota_sources[0][1], fetched_at=datetime.now(UTC))
        assert quota["evidence_state"] == "SUFFICIENT"
        assert quota["calculations"][0]["expected"] == quota["calculations"][0]["observed"] == 0
        assert quota["usage_bytes"] == 20_000_000_000

        session_id = uuid4()
        principal_id = f"quota-it-{session_id}"
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        facade = ResolveFacade(engine, provider, cursor_secret=b"quota-integration-test-secret-32b")
        conversation = facade.create_conversation(context)
        case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="DATA_DEPLETION", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(context, case_id=case["id"], expected_version=1,
            command_key=f"quota-it-{uuid4()}", complaint_type="DATA_DEPLETION", window_start=start,
            window_end=end, reported_facts={})
        view = InvestigationView.model_validate(investigation)
        assert view.evidence_state == "SUFFICIENT"
        assert {item.unit for item in view.calculations} == {"BYTES", "LKR_MINOR"}
        balance = next(item for item in view.calculations if item.unit == "LKR_MINOR")
        assert balance.expected == balance.observed == 2_000
        assert any(term.value == -8_000 for term in balance.terms)
        assert any(item.source_payload.get("kind") == "OUT_OF_BUNDLE_USAGE" for item in view.evidence)
    finally:
        engine.dispose()
