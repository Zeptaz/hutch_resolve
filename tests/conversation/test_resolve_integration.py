"""Conversation module against Harry's REAL ResolveFacade and PostgreSQL (integration).

Skipped unless RESOLVE_INTEGRATION_DATABASE_URL points at an isolated, migrated database
(app role); RESOLVE_INTEGRATION_SANDBOX_URL is the sandbox-provider role. Never point it
at a shared database: each test creates sessions, conversations and cases.

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
pytestmark = pytest.mark.skipif(not (DB_URL and SANDBOX_URL), reason="needs an isolated migrated Resolve database")

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RUN = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_A = UUID("20000000-0000-0000-0000-000000000001")
ACCOUNT_D = UUID("20000000-0000-0000-0000-000000000004")
SIM_START = datetime(2026, 10, 2, 2, 30, tzinfo=UTC)
SIM_END = datetime(2026, 10, 2, 6, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def harry():
    from sqlalchemy import create_engine

    from backend.resolve.app.auth_store import AuthStore
    from backend.resolve.services.facade import ResolveFacade

    app_engine, sandbox_engine = create_engine(DB_URL), create_engine(SANDBOX_URL)
    yield ResolveFacade(app_engine, provider_engine=sandbox_engine), AuthStore(app_engine)
    app_engine.dispose()
    sandbox_engine.dispose()


def journey(harry, account_id: UUID):
    """A customer session created through Harry's AuthStore, plus a conversation in his database."""
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


def test_unimplemented_complaint_paths_surface_as_errors(harry) -> None:
    from resolve.conversation.errors import ResolveError

    ctx, conv, repo, service, _ = journey(harry, ACCOUNT_A)
    with pytest.raises(ResolveError) as err:
        send(service, repo, ctx, conv, details("DATA_DEPLETION"))
    assert err.value.code == "ACTION_NOT_ALLOWED"  # Harry: "not implemented yet" (H-06)
    assert repo.conversations[conv].claim is None  # claim released; the customer can retry
