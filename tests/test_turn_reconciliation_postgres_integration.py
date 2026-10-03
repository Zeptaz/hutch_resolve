from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.turn_claims import claim_turn


@pytest.mark.skipif(not os.getenv("TURN_RECOVERY_DATABASE_URL"), reason="requires disposable migrated/seeded PostgreSQL")
def test_agent_reconciliation_closes_only_stalled_turn_and_prevents_replay():
    engine = create_engine(os.environ["TURN_RECOVERY_DATABASE_URL"])
    run_id = UUID(os.getenv("TURN_RECOVERY_RUN_ID", "00000000-0000-0000-0000-000000000001"))
    try:
        with engine.connect() as connection:
            account_id = connection.execute(text(
                "SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0001'"
            ), {"run": run_id}).scalar_one()
        now = datetime.now(UTC)
        customer_id, agent_id = uuid4(), uuid4()
        store = AuthStore(engine)
        for session_id, role, principal, account in (
            (customer_id, "CUSTOMER", f"turn-recovery-customer-{customer_id}", account_id),
            (agent_id, "AGENT", f"turn-recovery-agent-{agent_id}", None),
        ):
            store.create_session(session_id=session_id, credential_hash=uuid4().bytes,
                csrf_hash=uuid4().bytes, role=role, principal_id=principal,
                sandbox_id=run_id, account_id=account, expires_at=now + timedelta(hours=1))
        customer = AuthContext(customer_id, f"turn-recovery-customer-{customer_id}", "CUSTOMER",
                               run_id, account_id, uuid4(), "TEXT")
        agent = AuthContext(agent_id, f"turn-recovery-agent-{agent_id}", "AGENT",
                            run_id, None, uuid4(), "AGENT")
        facade = ResolveFacade(engine)
        conversation = facade.create_conversation(customer)
        turn_id = uuid4()
        claim = claim_turn(engine, sandbox_id=run_id, conversation_id=conversation["id"],
            turn_id=turn_id, input_hash="stalled-turn", input_payload={"channel": "TEXT"},
            expected_version=conversation["version"], now=now)
        start = datetime.fromisoformat("2026-10-02T11:00:00+05:30")
        end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
        case = facade.create_case(customer, conversation_id=conversation["id"],
            client_turn_id=turn_id, expected_conversation_version=conversation["version"],
            complaint_type="BALANCE_RECHARGE", window_start=start, window_end=end, reported_facts={})
        investigation = facade.investigate(customer, case_id=case["id"], expected_version=case["version"],
            command_key=f"turn-recovery-investigate-{uuid4()}", complaint_type="BALANCE_RECHARGE",
            window_start=start, window_end=end, reported_facts={})
        ticket = next(item for item in investigation["eligible_actions"]
                      if item["action_type"] == "CREATE_REVIEW_TICKET")
        current_case = facade.get_case(customer, case["id"])
        proposal = facade.propose_action(customer, case_id=case["id"], expected_version=current_case["version"],
            investigation_id=investigation["id"], action_type="CREATE_REVIEW_TICKET",
            target_id=ticket["target_id"], request_key=f"turn-recovery-proposal-{uuid4()}",
            escalation_reason="Customer requested a review of the recharge evidence.")
        accepted = facade.confirm_action(customer, proposal_id=proposal["id"],
            proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=uuid4())
        assert accepted["operation_status"] == "PENDING"
        with pytest.raises(ResolveError) as active:
            facade.reconcile_turn(agent, conversation_id=conversation["id"], turn_id=turn_id,
                note="Review the stalled case before settling its conversation turn.", now=now)
        assert active.value.code == "TURN_IN_PROGRESS"
        with engine.begin() as connection:
            connection.execute(text("UPDATE resolve.turn_claims SET lease_until=:expired WHERE conversation_id=:conversation AND client_turn_id=:turn"),
                {"expired": now - timedelta(minutes=1), "conversation": conversation["id"], "turn": turn_id})
        with pytest.raises(ResolveError) as unresolved:
            facade.reconcile_turn(agent, conversation_id=conversation["id"], turn_id=turn_id,
                note="Review the stalled case before settling its conversation turn.", now=now)
        assert unresolved.value.code == "TURN_OUTCOME_UNRESOLVED"
        assert str(accepted["operation_id"]) in unresolved.value.details["operation_ids"]
        with engine.begin() as connection:
            connection.execute(text("UPDATE resolve.operations SET status='FAILED' WHERE id=:id"),
                               {"id": accepted["operation_id"]})
        settled = facade.reconcile_turn(agent, conversation_id=conversation["id"], turn_id=turn_id,
            note="Reviewed the failed operation; no ambiguous outcome remains on this turn.", now=now)
        assert settled["state"] == "ABANDONED"
        assert UUID(str(case["id"])) in settled["case_ids"]
        assert settled["operations"][0]["status"] == "FAILED"
        with pytest.raises(ResolveError) as replay:
            claim_turn(engine, sandbox_id=run_id, conversation_id=conversation["id"],
                turn_id=turn_id, input_hash="stalled-turn", input_payload={"channel": "TEXT"}, now=now)
        assert replay.value.code == "TURN_ABANDONED"
        next_turn = claim_turn(engine, sandbox_id=run_id, conversation_id=conversation["id"],
            turn_id=uuid4(), input_hash="fresh-turn", input_payload={"channel": "TEXT"}, now=now)
        assert next_turn.token is not None
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TURN_RECOVERY_DATABASE_URL"), reason="requires disposable migrated/seeded PostgreSQL")
def test_turn_reconciliation_requires_agent_context():
    engine = create_engine(os.environ["TURN_RECOVERY_DATABASE_URL"])
    try:
        context = AuthContext(uuid4(), "customer", "CUSTOMER", uuid4(), uuid4(), uuid4(), "TEXT")
        with pytest.raises(ResolveError) as denied:
            ResolveFacade(engine).reconcile_turn(context, conversation_id=uuid4(), turn_id=uuid4(),
                note="Review is documented for operational recovery.")
        assert denied.value.code == "ROLE_FORBIDDEN"
    finally:
        engine.dispose()
