"""Opt-in: QUOTA_IT_DATABASE_URL must reference a disposable DB with migrated seed.sql fixtures."""
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.app.case_api import InvestigationView
from backend.resolve.providers.sandbox import PostgresSandboxProvider, reconcile_quota, reconcile_recharge_records, reconcile_service_status, reconcile_statement
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


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL"), reason="requires disposable seeded PostgreSQL")
def test_seeded_connectivity_case_uses_fresh_account_check_and_matching_incident():
    engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000003")
    start = datetime.fromisoformat("2026-10-02T11:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        provider = PostgresSandboxProvider(engine)
        service = provider.get_service_statement(run_id, account_id)
        assert service is not None and service.package_active
        finding = reconcile_service_status(service)
        assert finding["evidence_state"] == "SUFFICIENT"
        assert finding["findings"][0]["code"] == "NETWORK_INCIDENT_CONFIRMED"
        assert "No recovery ETA is available" in finding["findings"][0]["text"]

        session_id = uuid4()
        principal_id = f"connectivity-it-{session_id}"
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        facade = ResolveFacade(engine, provider, cursor_secret=b"connectivity-integration-test-32")
        conversation = facade.create_conversation(context)
        case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="CONNECTIVITY", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(context, case_id=case["id"], expected_version=1,
            command_key=f"connectivity-it-{uuid4()}", complaint_type="CONNECTIVITY", window_start=start,
            window_end=end, reported_facts={})
        view = InvestigationView.model_validate(investigation)
        assert view.evidence_state == "SUFFICIENT"
        assert view.findings[0].code == "NETWORK_INCIDENT_CONFIRMED"
        assert any(item.source == "SERVICE_ASSURANCE" for item in view.evidence)
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL"), reason="requires disposable seeded PostgreSQL")
def test_seeded_captured_recharge_pending_fulfilment_blocks_duplicate_payment_advice():
    engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000005")
    start = datetime.fromisoformat("2026-10-02T09:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        provider = PostgresSandboxProvider(engine)
        records, complete, source_version = provider.get_recharge_records(run_id, account_id, start, end, "SYN-E-RECHARGE")
        direct = reconcile_recharge_records(records, fetched_at=datetime.now(UTC), source_version=source_version,
            complete=complete, expected_reference="SYN-E-RECHARGE")
        assert direct["evidence_state"] == "SUFFICIENT"
        assert direct["findings"][0]["code"] == "PAYMENT_CAPTURED_FULFILMENT_PENDING"

        session_id = uuid4()
        principal_id = f"recharge-it-{session_id}"
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        facade = ResolveFacade(engine, provider, cursor_secret=b"recharge-integration-test-secret")
        conversation = facade.create_conversation(context)
        case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="BALANCE_RECHARGE", window_start=start,
            window_end=end, reported_facts={"recharge_reference": "SYN-E-RECHARGE"})
        investigation = facade.investigate(context, case_id=case["id"], expected_version=1,
            command_key=f"recharge-it-{uuid4()}", complaint_type="BALANCE_RECHARGE", window_start=start,
            window_end=end, reported_facts={"recharge_reference": "SYN-E-RECHARGE"})
        view = InvestigationView.model_validate(investigation)
        assert view.evidence_state == "SUFFICIENT"
        finding = next(item for item in view.findings if item.code == "PAYMENT_CAPTURED_FULFILMENT_PENDING")
        assert "Do not submit another payment" in finding.text
        assert any(item.source == "RECHARGE_FULFILMENT" for item in view.evidence)
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL"), reason="requires disposable seeded PostgreSQL")
def test_seeded_vas_without_activation_evidence_keeps_past_dispute_open_and_future_stop_separate():
    engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000006")
    start = datetime.fromisoformat("2026-10-02T09:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        provider = PostgresSandboxProvider(engine)
        session_id = uuid4()
        principal_id = f"vas-it-{session_id}"
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        facade = ResolveFacade(engine, provider, cursor_secret=b"vas-integration-test-secret-32b")
        conversation = facade.create_conversation(context)
        case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="VAS_DISPUTE", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(context, case_id=case["id"], expected_version=1,
            command_key=f"vas-it-{uuid4()}", complaint_type="VAS_DISPUTE", window_start=start,
            window_end=end, reported_facts={})
        view = InvestigationView.model_validate(investigation)
        assert view.evidence_state == "PARTIAL"
        assert "VAS_ACTIVATION_EVIDENCE_MISSING" in view.missing
        assert any(item.code == "VAS_ACTIVATION_UNVERIFIED" and "does not establish customer consent" in item.text
            for item in view.findings)
        action = next(item for item in investigation["eligible_actions"] if item["action_type"] == "DEACTIVATE_VAS")
        current = facade.get_case(context, case["id"])
        proposal = facade.propose_action(context, case_id=case["id"], expected_version=current["version"],
            investigation_id=investigation["id"], action_type="DEACTIVATE_VAS", target_id=action["target_id"],
            request_key=f"vas-proposal-{uuid4()}")
        assert "past charges remain under investigation" in proposal["consequences"]
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL") or not os.getenv("QUOTA_IT_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL read and sandbox-writer URLs")
def test_charging_fault_profiles_are_consumed_as_partial_or_conflicting_evidence():
    read_engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    write_engine = create_engine(os.environ["QUOTA_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    try:
        provider = PostgresSandboxProvider(read_engine, write_engine)
        with read_engine.connect() as connection:
            accounts = dict(connection.execute(text("""
                SELECT line_alias,id FROM sandbox.accounts WHERE sandbox_id=:run
            """), {"run": run_id}).all())
        a_id = next(value for alias, value in accounts.items() if alias.endswith("-0001"))
        d_id = next(value for alias, value in accounts.items() if alias.endswith("-0004"))
        start = datetime.fromisoformat("2026-10-02T08:00:00+05:30")
        end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")

        def apply_fault(account_id, account_label, fault_type, parameters):
            selector = json.dumps({"account": account_label})
            fault_id = uuid4()
            with write_engine.begin() as connection:
                connection.execute(text("""
                    UPDATE sandbox.fault_profiles SET remaining_uses=0
                    WHERE sandbox_id=:run AND provider='charging' AND operation='statement'
                      AND (selector='{}'::jsonb OR selector @> CAST(:selector AS jsonb))
                """), {"run": run_id, "selector": selector})
                connection.execute(text("""
                    INSERT INTO sandbox.fault_profiles(id,sandbox_id,provider,operation,selector,fault_type,parameters,remaining_uses)
                    VALUES (:id,:run,'charging','statement',CAST(:selector AS jsonb),:fault,CAST(:parameters AS jsonb),1)
                """), {"id": fault_id, "run": run_id, "selector": selector,
                    "fault": fault_type, "parameters": json.dumps(parameters)})
            statement = provider.get_statement(run_id, account_id, "MAIN", start, end)
            with write_engine.connect() as connection:
                remaining = connection.execute(text("""
                    SELECT remaining_uses FROM sandbox.fault_profiles
                    WHERE id=:id
                """), {"id": fault_id}).scalar_one()
            assert remaining == 0
            return statement

        late = apply_fault(a_id, "A", "LATE_POSTING", {"reference": "SYN-RECHARGE-A", "delay_seconds": 120})
        assert "LATE_POSTING_NOT_YET_VISIBLE" in late.warnings
        assert not late.complete
        assert reconcile_statement(late)["evidence_state"] == "PARTIAL"
        missing_opening = apply_fault(a_id, "A", "MISSING_OPENING_SNAPSHOT", {})
        assert missing_opening.opening is None
        assert reconcile_statement(missing_opening)["evidence_state"] == "PARTIAL"
        duplicate = apply_fault(a_id, "A", "DUPLICATE_POSTING", {"duplicate_reference": "SYN-RECHARGE-A"})
        assert reconcile_statement(duplicate)["evidence_state"] == "CONFLICTING"
        reversal = apply_fault(d_id, "D", "REVERSAL_MISMATCH", {"original_reference": "SYN-RECHARGE-D",
            "posting_seq": 4, "reversal_amount_minor": -99999})
        result = reconcile_statement(reversal)
        assert result["evidence_state"] == "CONFLICTING"
        assert "POSTING_REVERSAL_MISMATCH" in result["conflicts"]
    finally:
        read_engine.dispose()
        write_engine.dispose()


@pytest.mark.skipif(not os.getenv("QUOTA_IT_DATABASE_URL") or not os.getenv("QUOTA_IT_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL read and sandbox-writer URLs")
def test_seeded_usage_fault_profiles_are_one_shot_and_never_sufficient_on_bad_evidence():
    read_engine = create_engine(os.environ["QUOTA_IT_DATABASE_URL"])
    write_engine = create_engine(os.environ["QUOTA_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("QUOTA_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    start = datetime.fromisoformat("2026-10-02T08:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        provider = PostgresSandboxProvider(read_engine, write_engine)
        with read_engine.connect() as connection:
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias LIKE '%-0002'
            """), {"run": run_id}).scalar_one()
            profiles = dict(connection.execute(text("""
                SELECT fault_type,id FROM sandbox.fault_profiles
                WHERE sandbox_id=:run AND provider='usage' AND operation='list_usage'
            """), {"run": run_id}).all())
        assert {"INCOMPLETE_PAGE", "STALE_SOURCE", "WRONG_UNIT"} <= profiles.keys()

        expected = {
            "INCOMPLETE_PAGE": ("PARTIAL", "QUOTA_SOURCE_INCOMPLETE"),
            "STALE_SOURCE": ("PARTIAL", "QUOTA_SOURCE_INCOMPLETE"),
            "WRONG_UNIT": ("CONFLICTING", "USAGE_UNIT_MISMATCH"),
        }
        for fault_type, (expected_state, marker) in expected.items():
            with write_engine.begin() as connection:
                connection.execute(text("""
                    UPDATE sandbox.fault_profiles SET remaining_uses=0
                    WHERE sandbox_id=:run AND provider='usage' AND operation='list_usage'
                """), {"run": run_id})
                connection.execute(text("UPDATE sandbox.fault_profiles SET remaining_uses=1 WHERE id=:id"),
                    {"id": profiles[fault_type]})

            first_statement, first_usage = provider.get_quota_statements(run_id, account_id, start, end)[0]
            first = reconcile_quota(first_statement, first_usage, fetched_at=datetime.now(UTC))
            assert first["evidence_state"] == expected_state
            assert marker in (first["missing"] if expected_state == "PARTIAL" else first["conflicts"])
            if fault_type == "INCOMPLETE_PAGE":
                assert ":usage-incomplete-page-page-2" in first_statement.source_version
            elif fault_type == "STALE_SOURCE":
                assert ":usage-stale-600s" in first_statement.source_version
            else:
                usage_evidence = [item for item in first["evidence"] if item["source"] == "USAGE_RECORDS"]
                assert usage_evidence and all(item["unit"] == "KB" for item in usage_evidence)
                assert all(item["source_payload"]["unit_matches_contract"] is False for item in usage_evidence)

            with write_engine.connect() as connection:
                remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                    {"id": profiles[fault_type]}).scalar_one()
            assert remaining == 0

            clean_statement, clean_usage = provider.get_quota_statements(run_id, account_id, start, end)[0]
            clean = reconcile_quota(clean_statement, clean_usage, fetched_at=datetime.now(UTC))
            assert clean["evidence_state"] == "SUFFICIENT"
    finally:
        read_engine.dispose()
        write_engine.dispose()
