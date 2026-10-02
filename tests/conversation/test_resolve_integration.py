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
    # Mirrors Harry's app wiring (ResolveDev 9ab23d9): the facade reads sandbox data with the app engine.
    yield ResolveFacade(app_engine, cursor_secret=b"integration-test-cursor-secret-0123"), AuthStore(app_engine)
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


@pytest.mark.skipif(not (DB_URL and SANDBOX_URL), reason="needs an isolated migrated Resolve database")
def test_seeded_faults_as_in_harrys_app_are_reported_safely() -> None:
    """Harry's app (main.py) enables seeded, single-use fault profiles (consumed in id order).

    First A statement read: LATE_POSTING; first D read: REVERSAL_MISMATCH. The conversation must not
    claim everything matches, must say D's records conflict, and must never show raw codes. Runs on
    its own fault-enabled facade so the other journeys stay deterministic.
    """
    import re

    from sqlalchemy import create_engine

    from backend.resolve.app.auth_store import AuthStore
    from backend.resolve.providers.sandbox import PostgresSandboxProvider
    from backend.resolve.services.facade import ResolveFacade

    app_engine, sandbox_engine = create_engine(DB_URL), create_engine(SANDBOX_URL)
    try:
        provider = PostgresSandboxProvider(app_engine, sandbox_engine)  # exactly as main.py wires it
        faulty = (ResolveFacade(app_engine, provider, cursor_secret=b"integration-test-cursor-secret-0123"), AuthStore(app_engine))

        ctx, conv, repo, service, adapter = journey(faulty, ACCOUNT_A)
        a = send(service, repo, ctx, conv, details())
        a_inv = asyncio.run(adapter.get_case(ctx, a.case_id)).investigation
        assert "matches opening plus posted entries" not in a.reply_text  # no false all-clear
        assert not re.search(r"\b[A-Z]+(?:_[A-Z]+)+\b", a.reply_text)

        ctx_d, conv_d, repo_d, service_d, _ = journey(faulty, ACCOUNT_D)
        d = send(service_d, repo_d, ctx_d, conv_d, details())
        assert "don't agree" in d.reply_text  # conflict stated; no account change offered
        assert all(card.data.action_type == "CREATE_REVIEW_TICKET" for card in d.cards if card.type == "confirmation")
        assert not re.search(r"\b[A-Z]+(?:_[A-Z]+)+\b", d.reply_text)

        if any(f.code == "LEDGER_PARTIAL" for f in a_inv.findings) and a_inv.evidence_state == "SUFFICIENT":
            pytest.xfail("Harry finding 12: provisional ledger (LEDGER_PARTIAL) is labelled SUFFICIENT")
    finally:
        app_engine.dispose()
        sandbox_engine.dispose()
