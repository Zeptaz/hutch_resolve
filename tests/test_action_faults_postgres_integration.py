"""Opt-in action-worker fault checks against disposable migrated PostgreSQL databases."""
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner


pytestmark = pytest.mark.skipif(
    not os.getenv("OP_IT_DATABASE_URL") or not os.getenv("OP_IT_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL with Resolve and sandbox role URLs",
)


def _set_fault(sandbox_engine, run_id: UUID, provider: str, operation: str, fault_type: str | None) -> UUID | None:
    """Enable just one seeded fault in the selected provider-operation group."""
    with sandbox_engine.begin() as connection:
        connection.execute(text("""
            UPDATE sandbox.fault_profiles SET remaining_uses=0
            WHERE sandbox_id=:run AND provider=:provider AND operation=:operation
        """), {"run": run_id, "provider": provider, "operation": operation})
        if fault_type is None:
            return None
        profile_id = connection.execute(text("""
            UPDATE sandbox.fault_profiles SET remaining_uses=1
            WHERE sandbox_id=:run AND provider=:provider AND operation=:operation AND fault_type=:fault
            RETURNING id
        """), {"run": run_id, "provider": provider, "operation": operation, "fault": fault_type}).scalar_one()
        return profile_id


def _prepare_action(resolve_engine, run_id: UUID, account_alias: str, complaint_type: str,
                    action_type: str) -> tuple[ResolveFacade, AuthContext, UUID, UUID]:
    """Create a fresh customer session, investigation, proposal, and accepted operation."""
    with resolve_engine.connect() as connection:
        account_id = connection.execute(text("""
            SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias=:alias
        """), {"run": run_id, "alias": account_alias}).scalar_one()

    session_id = uuid4()
    principal_id = f"action-fault-it-{session_id}"
    AuthStore(resolve_engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
        csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id, sandbox_id=run_id,
        account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    context = AuthContext(session_id, principal_id, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
    facade = ResolveFacade(resolve_engine, PostgresSandboxProvider(resolve_engine),
        cursor_secret=b"action-fault-postgres-integration-secret")
    conversation = facade.create_conversation(context)
    if complaint_type == "CONNECTIVITY":
        start = datetime.fromisoformat("2026-10-02T11:00:00+05:30")
    else:
        start = datetime.fromisoformat("2026-10-02T09:00:00+05:30")
    end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
    case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
        expected_conversation_version=1, complaint_type=complaint_type, window_start=start,
        window_end=end, reported_facts={})
    investigation = facade.investigate(context, case_id=case["id"], expected_version=case["version"],
        command_key=f"action-fault-investigation-{uuid4()}", complaint_type=complaint_type,
        window_start=start, window_end=end, reported_facts={})
    eligible = next(item for item in investigation["eligible_actions"] if item["action_type"] == action_type)
    current = facade.get_case(context, case["id"])
    proposal = facade.propose_action(context, case_id=case["id"], expected_version=current["version"],
        investigation_id=investigation["id"], action_type=action_type, target_id=eligible["target_id"],
        request_key=f"action-fault-proposal-{uuid4()}")
    confirmation = facade.confirm_action(context, proposal_id=proposal["id"],
        proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=uuid4())
    return facade, context, case["id"], confirmation["operation_id"]


def _resolve_state(resolve_engine, case_id: UUID, operation_id: UUID) -> dict:
    with resolve_engine.connect() as connection:
        operation = connection.execute(text("""
            SELECT status,outcome,attempt_count,recovery_after FROM resolve.operations WHERE id=:id
        """), {"id": operation_id}).mappings().one()
        case_status = connection.execute(text("SELECT status FROM resolve.cases WHERE id=:id"),
            {"id": case_id}).scalar_one()
        receipts = connection.execute(text("SELECT count(*) FROM resolve.receipts WHERE case_id=:id"),
            {"id": case_id}).scalar_one()
        delivery = connection.execute(text("""
            SELECT delivery_state,provider_ticket_id FROM resolve.escalation_deliveries WHERE case_id=:case
        """), {"case": case_id}).mappings().one_or_none()
    return {"operation": operation, "case_status": case_status, "receipts": receipts, "delivery": delivery}


def test_seeded_crm_unavailable_is_unknown_then_recovers_once_after_one_shot_fault():
    resolve_engine = create_engine(os.environ["OP_IT_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("OP_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    try:
        profile_id = _set_fault(sandbox_engine, run_id, "crm", "create_ticket", "PROVIDER_UNAVAILABLE")
        facade, context, case_id, operation_id = _prepare_action(resolve_engine, run_id, "SIM-LK-0003",
            "CONNECTIVITY", "CREATE_REVIEW_TICKET")

        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        first = _resolve_state(resolve_engine, case_id, operation_id)
        assert first["operation"]["status"] == "UNKNOWN"
        assert first["operation"]["outcome"]["code"] == "PROVIDER_UNAVAILABLE"
        assert first["operation"]["attempt_count"] == 1
        assert first["case_status"] == "ACTION_PENDING"
        assert first["receipts"] == 0
        assert first["delivery"]["delivery_state"] == "PENDING"
        assert first["delivery"]["provider_ticket_id"] is None
        with sandbox_engine.connect() as connection:
            remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                {"id": profile_id}).scalar_one()
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='crm'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            tickets = connection.execute(text("""
                SELECT count(*) FROM sandbox.tickets WHERE sandbox_id=:run AND case_ref=:reference
            """), {"run": run_id, "reference": f"RESOLVE-{operation_id}"}).scalar_one()
        assert remaining == 0
        assert writes == tickets == 0

        with resolve_engine.begin() as connection:
            connection.execute(text("UPDATE resolve.operations SET recovery_after=now()-interval '1 second' WHERE id=:id"),
                {"id": operation_id})
        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        recovered = _resolve_state(resolve_engine, case_id, operation_id)
        assert recovered["operation"]["status"] == "SUCCEEDED"
        assert recovered["operation"]["attempt_count"] == 2
        assert recovered["case_status"] == "REVIEW_REQUIRED"
        assert recovered["receipts"] == 1
        assert recovered["delivery"]["delivery_state"] == "DELIVERED"
        assert recovered["delivery"]["provider_ticket_id"]
        with sandbox_engine.connect() as connection:
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='crm'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            tickets = connection.execute(text("""
                SELECT count(*) FROM sandbox.tickets WHERE sandbox_id=:run AND case_ref=:reference
            """), {"run": run_id, "reference": f"RESOLVE-{operation_id}"}).scalar_one()
        assert writes == tickets == 1
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()


def test_seeded_vas_write_rejected_is_terminal_and_does_not_change_subscription():
    resolve_engine = create_engine(os.environ["OP_IT_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("OP_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    try:
        with resolve_engine.connect() as connection:
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0001'
            """), {"run": run_id}).scalar_one()
            target_id = connection.execute(text("""
                SELECT s.id FROM sandbox.subscriptions s JOIN sandbox.offers o
                  ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                WHERE s.sandbox_id=:run AND s.account_id=:account AND o.offer_kind='VAS'
                  AND s.status='ACTIVE' AND s.renew_enabled
            """), {"run": run_id, "account": account_id}).scalar_one()
            before = connection.execute(text("""
                SELECT version,status,renew_enabled FROM sandbox.subscriptions WHERE sandbox_id=:run AND id=:id
            """), {"run": run_id, "id": target_id}).mappings().one()
        profile_id = _set_fault(sandbox_engine, run_id, "vas", "deactivate", "WRITE_REJECTED")
        facade, context, case_id, operation_id = _prepare_action(resolve_engine, run_id, "SIM-LK-0001",
            "VAS_DISPUTE", "DEACTIVATE_VAS")
        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        state = _resolve_state(resolve_engine, case_id, operation_id)
        assert state["operation"]["status"] == "FAILED"
        assert state["operation"]["outcome"]["code"] == "PROVIDER_REJECTED"
        assert state["operation"]["attempt_count"] == 1
        assert state["operation"]["recovery_after"] is None
        assert state["case_status"] == "REVIEW_REQUIRED"
        assert state["receipts"] == 1
        with sandbox_engine.connect() as connection:
            after = connection.execute(text("""
                SELECT version,status,renew_enabled FROM sandbox.subscriptions WHERE sandbox_id=:run AND id=:id
            """), {"run": run_id, "id": target_id}).mappings().one()
            events = connection.execute(text("""
                SELECT count(*) FROM sandbox.subscription_events WHERE sandbox_id=:run AND subscription_id=:id
                  AND event_type='AUTO_RENEW_CANCELLED'
            """), {"run": run_id, "id": target_id}).scalar_one()
            provider_operation = connection.execute(text("""
                SELECT status,result FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='vas'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).mappings().one()
            remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                {"id": profile_id}).scalar_one()
        assert dict(after) == dict(before)
        assert events == 0
        assert provider_operation["status"] == "FAILED"
        assert provider_operation["result"]["code"] == "PROVIDER_REJECTED"
        assert remaining == 0
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()


def test_lookup_unavailable_recovery_uses_existing_provider_operation_without_duplicate_write():
    """A committed lost response is reconciled via the operation lookup fault and stable key."""
    resolve_engine = create_engine(os.environ["OP_IT_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("OP_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    try:
        lost_profile_id = _set_fault(sandbox_engine, run_id, "vas", "deactivate", "COMMITTED_RESPONSE_LOST")
        lookup_profile_id = _set_fault(sandbox_engine, run_id, "operations", "lookup", "LOOKUP_UNAVAILABLE")
        _, _, case_id, operation_id = _prepare_action(resolve_engine, run_id, "SIM-LK-0001",
            "VAS_DISPUTE", "DEACTIVATE_VAS")
        with sandbox_engine.connect() as connection:
            target_id = connection.execute(text("""
                SELECT s.id FROM sandbox.subscriptions s JOIN sandbox.offers o
                  ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                JOIN sandbox.accounts a ON (a.sandbox_id,a.id)=(s.sandbox_id,s.account_id)
                WHERE s.sandbox_id=:run AND a.line_alias='SIM-LK-0001' AND o.offer_kind='VAS'
                  AND s.status='ACTIVE' AND s.renew_enabled
            """), {"run": run_id}).scalar_one()
        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        lost = _resolve_state(resolve_engine, case_id, operation_id)
        assert lost["operation"]["status"] == "UNKNOWN"
        assert lost["operation"]["outcome"]["code"] == "COMMITTED_RESPONSE_LOST"
        assert lost["operation"]["attempt_count"] == 1
        with sandbox_engine.connect() as connection:
            lost_remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                {"id": lost_profile_id}).scalar_one()
            lookup_remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                {"id": lookup_profile_id}).scalar_one()
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='vas'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            target = connection.execute(text("""
                SELECT version,status,renew_enabled FROM sandbox.subscriptions
                WHERE sandbox_id=:run AND id=:id
            """), {"run": run_id, "id": target_id}).mappings().one()
            events = connection.execute(text("""
                SELECT count(*) FROM sandbox.subscription_events WHERE sandbox_id=:run
                  AND subscription_id=:id AND event_type='AUTO_RENEW_CANCELLED'
            """), {"run": run_id, "id": target_id}).scalar_one()
        assert lost_remaining == 0
        assert lookup_remaining == 1
        assert writes == events == 1
        assert target["version"] == 2 and target["status"] == "CANCELLED" and not target["renew_enabled"]

        with resolve_engine.begin() as connection:
            connection.execute(text("UPDATE resolve.operations SET recovery_after=now()-interval '1 second' WHERE id=:id"),
                {"id": operation_id})
        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        lookup_failed = _resolve_state(resolve_engine, case_id, operation_id)
        assert lookup_failed["operation"]["status"] == "UNKNOWN"
        assert lookup_failed["operation"]["outcome"]["code"] == "LOOKUP_UNAVAILABLE"
        assert lookup_failed["operation"]["attempt_count"] == 2
        assert lookup_failed["receipts"] == 0
        with sandbox_engine.connect() as connection:
            lookup_remaining = connection.execute(text("SELECT remaining_uses FROM sandbox.fault_profiles WHERE id=:id"),
                {"id": lookup_profile_id}).scalar_one()
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='vas'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            events = connection.execute(text("""
                SELECT count(*) FROM sandbox.subscription_events WHERE sandbox_id=:run
                  AND subscription_id=:id AND event_type='AUTO_RENEW_CANCELLED'
            """), {"run": run_id, "id": target_id}).scalar_one()
        assert lookup_remaining == 0
        assert writes == events == 1

        with resolve_engine.begin() as connection:
            connection.execute(text("UPDATE resolve.operations SET recovery_after=now()-interval '1 second' WHERE id=:id"),
                {"id": operation_id})
        assert OperationRunner(resolve_engine, sandbox_engine).run_once() is True
        settled = _resolve_state(resolve_engine, case_id, operation_id)
        assert settled["operation"]["status"] == "SUCCEEDED"
        assert settled["operation"]["attempt_count"] == 3
        assert settled["receipts"] == 1
        with sandbox_engine.connect() as connection:
            writes = connection.execute(text("""
                SELECT count(*) FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='vas'
                  AND idempotency_key=:key
            """), {"run": run_id, "key": str(operation_id)}).scalar_one()
            events = connection.execute(text("""
                SELECT count(*) FROM sandbox.subscription_events WHERE sandbox_id=:run
                  AND subscription_id=:id AND event_type='AUTO_RENEW_CANCELLED'
            """), {"run": run_id, "id": target_id}).scalar_one()
            target = connection.execute(text("""
                SELECT version,status,renew_enabled FROM sandbox.subscriptions
                WHERE sandbox_id=:run AND id=:id
            """), {"run": run_id, "id": target_id}).mappings().one()
        assert writes == events == 1
        assert target["version"] == 2 and target["status"] == "CANCELLED" and not target["renew_enabled"]
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()
