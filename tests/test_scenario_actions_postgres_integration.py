"""Scenario A/D actions on a disposable migrated, seeded PostgreSQL (opt-in).

SCENARIO_IT_DATABASE_URL: Resolve runtime role on a fresh database (base fixture run). The provider
is built without a fault engine, so seeded one-shot faults are not consumed here.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade

URL = os.getenv("SCENARIO_IT_DATABASE_URL")
RUN = UUID("00000000-0000-0000-0000-000000000001")
pytestmark = pytest.mark.skipif(not URL, reason="requires a disposable migrated, seeded PostgreSQL")

# "This morning" as the conversation resolves it: local midnight to the simulation clock.
MORNING_START = datetime.fromisoformat("2026-10-02T00:00:00+05:30")
MORNING_END = datetime.fromisoformat("2026-10-02T12:00:00+05:30")


def _investigate(engine, line_alias: str):
    with engine.connect() as connection:
        account_id = connection.execute(text(
            "SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias=:alias"),
            {"run": RUN, "alias": line_alias}).scalar_one()
    session_id = uuid4()
    AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes, csrf_hash=uuid4().bytes,
        role="CUSTOMER", principal_id=f"scenario-it-{session_id}", sandbox_id=RUN, account_id=account_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1))
    context = AuthContext(session_id, f"scenario-it-{session_id}", "CUSTOMER", RUN, account_id, uuid4(), "TEXT")
    facade = ResolveFacade(engine, PostgresSandboxProvider(engine))
    conversation = facade.create_conversation(context)
    case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
        expected_conversation_version=1, complaint_type="BALANCE_RECHARGE",
        window_start=MORNING_START, window_end=MORNING_END, reported_facts={})
    investigation = facade.investigate(context, case_id=case["id"], expected_version=case["version"],
        command_key=f"scenario-it-{uuid4()}", complaint_type="BALANCE_RECHARGE",
        window_start=MORNING_START, window_end=MORNING_END, reported_facts={})
    return facade, context, case, investigation


@pytest.fixture
def engine():
    engine = create_engine(URL)
    yield engine
    engine.dispose()


def test_a_reconciles_this_morning_and_offers_the_vas_stop_first(engine):
    facade, context, case, investigation = _investigate(engine, "SIM-LK-0001")
    assert investigation["evidence_state"] == "SUFFICIENT"
    actions = [item["action_type"] for item in investigation["eligible_actions"]]
    assert actions[0] == "DEACTIVATE_VAS" and "CREATE_REVIEW_TICKET" in actions
    vas = investigation["eligible_actions"][0]
    current = facade.get_case(context, case["id"])
    proposal = facade.propose_action(context, case_id=case["id"], expected_version=current["version"],
        investigation_id=investigation["id"], action_type="DEACTIVATE_VAS", target_id=vas["target_id"],
        request_key=f"scenario-it-vas-{uuid4()}")
    assert proposal["action_type"] == "DEACTIVATE_VAS"
    assert proposal["consequences"].startswith("Stop future renewals")
    assert proposal.get("package_terms") is None


def test_d_conflict_never_offers_an_account_change(engine):
    _, _, _, investigation = _investigate(engine, "SIM-LK-0004")
    assert investigation["evidence_state"] == "CONFLICTING"
    assert [item["action_type"] for item in investigation["eligible_actions"]] == ["CREATE_REVIEW_TICKET"]
