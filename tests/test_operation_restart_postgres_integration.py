"""Opt-in crash recovery check against a disposable migrated Resolve + sandbox database."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner


@pytest.mark.skipif(not os.getenv("OP_IT_DATABASE_URL") or not os.getenv("OP_IT_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL with Resolve and sandbox role URLs")
def test_action_worker_restart_recovers_committed_lost_response_once():
    resolve_engine = create_engine(os.environ["OP_IT_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("OP_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    session_id = uuid4()
    principal_id = f"restart-it-{session_id}"
    start = datetime.fromisoformat("2026-10-02T09:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        with resolve_engine.connect() as connection:
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0006'
            """), {"run": run_id}).scalar_one()
        customer = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        AuthStore(resolve_engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        provider = PostgresSandboxProvider(resolve_engine)
        facade = ResolveFacade(resolve_engine, provider, cursor_secret=b"operation-restart-integration-secret")
        conversation = facade.create_conversation(customer)
        case = facade.create_case(customer, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="VAS_DISPUTE", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(customer, case_id=case["id"], expected_version=1,
            command_key=f"restart-it-{uuid4()}", complaint_type="VAS_DISPUTE",
            window_start=start, window_end=end, reported_facts={})
        target = next(item for item in investigation["eligible_actions"] if item["action_type"] == "DEACTIVATE_VAS")
        current_case = facade.get_case(customer, case["id"])
        proposal = facade.propose_action(customer, case_id=case["id"], expected_version=current_case["version"],
            investigation_id=investigation["id"], action_type="DEACTIVATE_VAS", target_id=target["target_id"],
            request_key=f"restart-proposal-{uuid4()}")
        confirmation = facade.confirm_action(customer, proposal_id=proposal["id"],
            proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=uuid4())
        operation_id = confirmation["operation_id"]

        # Keep this test focused on a single crash/restart recovery. The
        # dedicated action-fault integration test covers lookup unavailability.
        with sandbox_engine.begin() as connection:
            connection.execute(text("""
                UPDATE sandbox.fault_profiles SET remaining_uses=0
                WHERE sandbox_id=:run AND provider='operations' AND operation='lookup'
            """), {"run": run_id})
            connection.execute(text("""
                UPDATE sandbox.fault_profiles SET remaining_uses=0
                WHERE sandbox_id=:run AND provider='vas' AND operation='deactivate'
            """), {"run": run_id})
            connection.execute(text("""
                UPDATE sandbox.fault_profiles SET remaining_uses=1
                WHERE sandbox_id=:run AND provider='vas' AND operation='deactivate'
                  AND fault_type='COMMITTED_RESPONSE_LOST'
            """), {"run": run_id})

        # The seeded VAS profile commits the stable provider operation, then loses
        # its response. The worker records UNKNOWN; simulate process death before
        # that local state is safely persisted by leaving an expired RUNNING lease.
        first_process = OperationRunner(resolve_engine, sandbox_engine)
        assert first_process.run_once() is True
        with resolve_engine.begin() as connection:
            connection.execute(text("""
                UPDATE resolve.operations SET status='RUNNING',lease_until=now()-interval '1 second',
                  recovery_after=NULL WHERE id=:id
            """), {"id": operation_id})

        restarted_process = OperationRunner(resolve_engine, sandbox_engine)
        assert restarted_process.run_once() is True
        with resolve_engine.connect() as connection:
            operation = connection.execute(text("""
                SELECT status,attempt_count FROM resolve.operations WHERE id=:id
            """), {"id": operation_id}).mappings().one()
            receipt_count = connection.execute(text("""
                SELECT count(*) FROM resolve.receipts WHERE case_id=:case
            """), {"case": case["id"]}).scalar_one()
        with sandbox_engine.connect() as connection:
            subscription = connection.execute(text("""
                SELECT version,status,renew_enabled FROM sandbox.subscriptions
                WHERE sandbox_id=:run AND account_id=:account AND id=:target
            """), {"run": run_id, "account": account_id, "target": target["target_id"]}).mappings().one()
            provider_writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations
                WHERE sandbox_id=:run AND provider='vas' AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            events = connection.execute(text("""
                SELECT count(*) FROM sandbox.subscription_events
                WHERE sandbox_id=:run AND subscription_id=:target AND event_type='AUTO_RENEW_CANCELLED'
            """), {"run": run_id, "target": target["target_id"]}).scalar_one()
        assert operation["status"] == "SUCCEEDED"
        assert operation["attempt_count"] == 2
        assert subscription["version"] == 2
        assert subscription["status"] == "CANCELLED" and not subscription["renew_enabled"]
        assert provider_writes == 1
        assert events == 1
        assert receipt_count == 1
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()


@pytest.mark.skipif(not os.getenv("OP_IT_DATABASE_URL") or not os.getenv("OP_IT_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL with Resolve and sandbox role URLs")
def test_two_workers_claim_a_pending_ticket_action_once():
    resolve_engine = create_engine(os.environ["OP_IT_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("OP_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    session_id = uuid4()
    principal_id = f"concurrent-worker-it-{session_id}"
    start = datetime.fromisoformat("2026-10-02T11:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    try:
        with resolve_engine.connect() as connection:
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0003'
            """), {"run": run_id}).scalar_one()
        customer = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        AuthStore(resolve_engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        with sandbox_engine.begin() as connection:
            connection.execute(text("""
                UPDATE sandbox.fault_profiles SET remaining_uses=0
                WHERE sandbox_id=:run AND provider='crm' AND operation='create_ticket'
            """), {"run": run_id})
        provider = PostgresSandboxProvider(resolve_engine)
        facade = ResolveFacade(resolve_engine, provider, cursor_secret=b"operation-concurrency-integration")
        conversation = facade.create_conversation(customer)
        case = facade.create_case(customer, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="CONNECTIVITY", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(customer, case_id=case["id"], expected_version=1,
            command_key=f"concurrency-it-{uuid4()}", complaint_type="CONNECTIVITY",
            window_start=start, window_end=end, reported_facts={})
        current_case = facade.get_case(customer, case["id"])
        proposal = facade.propose_action(customer, case_id=case["id"], expected_version=current_case["version"],
            investigation_id=investigation["id"], action_type="CREATE_REVIEW_TICKET", target_id=account_id,
            request_key=f"concurrent-ticket-{uuid4()}",
            escalation_reason="Customer requested a connectivity review.")
        confirmation = facade.confirm_action(customer, proposal_id=proposal["id"],
            proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=uuid4())
        runner_a = OperationRunner(resolve_engine, sandbox_engine)
        runner_b = OperationRunner(resolve_engine, sandbox_engine)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda runner: runner.run_once(), (runner_a, runner_b)))
        assert sum(results) == 1
        with resolve_engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM resolve.operations WHERE id=:id"),
                {"id": confirmation["operation_id"]}).scalar_one()
            receipts = connection.execute(text("SELECT count(*) FROM resolve.receipts WHERE case_id=:case"),
                {"case": case["id"]}).scalar_one()
        with sandbox_engine.connect() as connection:
            tickets = connection.execute(text("""
                SELECT count(*) FROM sandbox.tickets WHERE sandbox_id=:run AND case_ref=:reference
            """), {"run": run_id, "reference": f"RESOLVE-{confirmation['operation_id']}"}).scalar_one()
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run
                  AND provider='crm' AND idempotency_key=:key
            """), {"run": run_id, "key": str(confirmation["operation_id"])}).scalar_one()
        assert status == "SUCCEEDED"
        assert receipts == tickets == writes == 1
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()
