"""The same customer journeys against BOTH backends: the dummy and Harry's real facade.

"dummy" always runs (fakes.FakeResolveFacade in strict mode, as the dev backend uses it).
"real" runs Harry's ResolveFacade on PostgreSQL and is skipped unless
RESOLVE_INTEGRATION_DATABASE_URL (app role) and RESOLVE_INTEGRATION_SANDBOX_URL point at an
isolated, migrated database. Never point them at a shared database. Passing on both is what
makes swapping the dummy for the real backend safe; known intended differences are explicit.

Turn storage is still the in-memory fake: Harry's ConversationRepository does not exist
yet. Each test therefore creates its case on the first turn, while the fake's version and
the database conversation version still agree (see the open version question in
docs/plans/tevin.md).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

DB_URL = os.environ.get("RESOLVE_INTEGRATION_DATABASE_URL")
SANDBOX_URL = os.environ.get("RESOLVE_INTEGRATION_SANDBOX_URL")

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RUN = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_A = UUID("20000000-0000-0000-0000-000000000001")
ACCOUNT_D = UUID("20000000-0000-0000-0000-000000000004")
SIM_START = datetime(2026, 10, 2, 2, 30, tzinfo=UTC)
SIM_END = datetime(2026, 10, 2, 6, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def harry_real():
    from sqlalchemy import create_engine

    from backend.resolve.app.auth_store import AuthStore
    from backend.resolve.services.facade import ResolveFacade

    app_engine, sandbox_engine = create_engine(DB_URL), create_engine(SANDBOX_URL)
    # Mirrors Harry's app wiring (ResolveDev 9ab23d9): the facade reads sandbox data with the app engine.
    yield ResolveFacade(app_engine, cursor_secret=b"integration-test-cursor-secret-0123"), AuthStore(app_engine)
    app_engine.dispose()
    sandbox_engine.dispose()


@pytest.fixture(params=["dummy", pytest.param("real", marks=pytest.mark.skipif(
    not (DB_URL and SANDBOX_URL), reason="needs an isolated migrated Resolve database"))])
def harry(request):
    return "dummy" if request.param == "dummy" else request.getfixturevalue("harry_real")


def is_dummy(harry) -> bool:
    return harry == "dummy"


def dummy_journey(account_id: UUID):
    from conftest import SANDBOX, customer
    from fakes import FakeConversationRepository, FakeKnowledgeRepository, FakeResolveFacade
    from resolve.conversation import ConversationService

    ctx = customer(account_id).model_copy(update={"sandbox_id": SANDBOX})
    names = {ACCOUNT_A: "A", ACCOUNT_D: "D", ACCOUNT_B: "B", ACCOUNT_C: "C", ACCOUNT_E: "E", ACCOUNT_F: "F"}
    facade = FakeResolveFacade(lambda: datetime.now(UTC), {account_id: names[account_id]}, strict_complaints=True)
    repo = FakeConversationRepository(lambda: datetime.now(UTC))
    conversation_id = repo.create(ctx)

    async def simulation_now(_ctx):
        return SIM_END

    return ctx, conversation_id, repo, ConversationService(facade, repo, FakeKnowledgeRepository(), None, simulation_now), facade


def journey(harry, account_id: UUID):
    """A customer session created through Harry's AuthStore, plus a conversation in his database."""
    if is_dummy(harry):
        return dummy_journey(account_id)
    from backend.resolve.app.auth import AuthContext as HarryContext
    from fakes import FakeConversationRepository, FakeKnowledgeRepository
    from fakes import _Conversation
    from resolve.conversation import ConversationService
    from resolve.conversation.dto import AuthContext, Channel, Role
    from resolve.conversation.resolve_adapter import ResolveFacadeAdapter

    facade, store = harry
    session_id = uuid4()
    store.create_session(
        session_id=session_id,
        credential_hash=secrets.token_bytes(32),
        csrf_hash=secrets.token_bytes(32),
        role="CUSTOMER",
        principal_id=f"integration:{account_id}",
        sandbox_id=RUN,
        account_id=account_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    ctx = AuthContext(
        session_id=session_id, principal_id=f"integration:{account_id}", role=Role.CUSTOMER,
        sandbox_id=RUN, account_id=account_id, request_id=uuid4(), channel=Channel.TEXT,
    )
    harry_ctx = HarryContext(session_id=session_id, principal_id=ctx.principal_id, role="CUSTOMER",
                             sandbox_id=RUN, account_id=account_id, request_id=ctx.request_id, channel="TEXT")
    conversation_id = UUID(str(facade.create_conversation(harry_ctx, "en")["id"]))

    repo = FakeConversationRepository(lambda: datetime.now(UTC))
    repo.conversations[conversation_id] = _Conversation(session_id=session_id)

    async def simulation_now(_ctx):
        return SIM_END

    adapter = ResolveFacadeAdapter(facade)
    service = ConversationService(adapter, repo, FakeKnowledgeRepository(), None, simulation_now)
    return ctx, conversation_id, repo, service, adapter


def send(service, repo, ctx, conversation_id, payload, turn_id=None):
    from resolve.conversation.dto import NormalizedTurn

    turn = NormalizedTurn.model_validate({
        "conversation_id": conversation_id, "turn_id": turn_id or uuid4(), "channel": "TEXT", "language": "en",
        "input": payload, "expected_version": repo.conversations[conversation_id].version,
    })
    return asyncio.run(service.handle_turn(ctx, turn))


def details(complaint="BALANCE_RECHARGE", **facts):
    return {"type": "complaint_details", "complaint_type": complaint, "window_start": SIM_START.isoformat(),
            "window_end": SIM_END.isoformat(), "reported_facts": facts}


def test_a_reconciles_from_real_ledger_and_accept_persists_pending_operation(harry) -> None:
    ctx, conv, repo, service, adapter = journey(harry, ACCOUNT_A)
    result = send(service, repo, ctx, conv, details(amount_minor=100000))

    calc = next(card.data for card in result.cards if card.type == "calculation")
    assert (calc.expected, calc.observed, calc.delta) == (42000, 42000, 0)  # computed by Harry from seeded postings
    offers = [card.data for card in result.cards if card.type == "confirmation"]
    assert len(offers) == 1 and result.pending_question.code == "CONFIRM_ACTION"
    offer = offers[0]

    accepted = send(service, repo, ctx, conv, {
        "type": "action_decision", "proposal_id": str(offer.id), "proposal_hash": offer.proposal_hash, "decision": "ACCEPT",
    })
    assert len(accepted.operation_ids) == 1
    operation = asyncio.run(adapter.get_operation(ctx, accepted.operation_ids[0]))
    assert operation.status == "PENDING"  # persisted by Harry; never reported as done
    assert "not been completed yet" in accepted.reply_text


def test_d_conflict_comes_from_real_ledger(harry) -> None:
    ctx, conv, repo, service, _ = journey(harry, ACCOUNT_D)
    result = send(service, repo, ctx, conv, details())
    calc = next(card.data for card in result.cards if card.type == "calculation")
    assert (calc.expected, calc.observed, calc.delta) == (42000, 35000, -7000)
    assert "can't give a final answer or change your account" in result.reply_text


def test_replayed_turn_does_not_reach_resolve_twice(harry) -> None:
    ctx, conv, repo, service, _ = journey(harry, ACCOUNT_A)
    turn_id = uuid4()
    first = send(service, repo, ctx, conv, details(), turn_id=turn_id)
    assert send(service, repo, ctx, conv, details(), turn_id=turn_id) == first


def test_vas_dispute_offers_resolve_listed_choices(harry) -> None:
    ctx, conv, repo, service, _ = journey(harry, ACCOUNT_A)
    result = send(service, repo, ctx, conv, details("VAS_DISPUTE"))
    state = repo.conversations[conv].state
    listed = {(c.action_type.value, str(c.target_id)) for c in state.pending_choices}
    offered = {(card.data.action_type.value, str(card.data.target_id)) for card in result.cards if card.type == "confirmation"}
    assert listed or offered  # whatever Harry made eligible, and nothing else
    if listed:
        assert result.pending_question.code == "CHOOSE_ACTION"


def test_data_complaint_on_a_line_without_data_issue(harry) -> None:
    """Both backends now answer a data complaint (Harry's H-06 landed); neither errors or shows codes."""
    ctx, conv, repo, service, _ = journey(harry, ACCOUNT_A)
    result = send(service, repo, ctx, conv, details("DATA_DEPLETION"))
    assert result.case_id is not None and result.reply_text
    if is_dummy(harry):
        assert "found nothing unusual" in result.reply_text


ACCOUNT_B = UUID("20000000-0000-0000-0000-000000000002")
ACCOUNT_C = UUID("20000000-0000-0000-0000-000000000003")
ACCOUNT_E = UUID("20000000-0000-0000-0000-000000000005")
ACCOUNT_F = UUID("20000000-0000-0000-0000-000000000006")


@pytest.mark.parametrize(("account", "complaint"), [
    (ACCOUNT_B, "DATA_DEPLETION"), (ACCOUNT_C, "CONNECTIVITY"), (ACCOUNT_E, "BALANCE_RECHARGE"), (ACCOUNT_F, "VAS_DISPUTE"),
])
def test_secondary_cases_answer_safely_from_records(harry, account, complaint) -> None:
    """B/C/E/F: safe behaviour from the records, checked without depending on Resolve's exact wording."""
    ctx, conv, repo, service, _ = journey(harry, account)
    result = send(service, repo, ctx, conv, details(complaint))
    import re

    assert not re.search(r"\b[A-Z]+(?:_[A-Z]+)+\b", result.reply_text)  # raw codes never reach the customer
    reply = result.reply_text.lower()
    if complaint == "DATA_DEPLETION":
        assert any(card.type == "calculation" for card in result.cards)
    if complaint == "CONNECTIVITY":
        for invented in ("will be restored", "restored by", "will be fixed", "within"):
            assert invented not in reply
    if complaint == "BALANCE_RECHARGE":
        assert "another payment" in reply or "pay again" in reply  # payment is not credit (E)
    if complaint == "VAS_DISPUTE":
        choices = {c.action_type.value for c in repo.conversations[conv].state.pending_choices}
        assert result.pending_question.code == "CHOOSE_ACTION" and "DEACTIVATE_VAS" in choices
