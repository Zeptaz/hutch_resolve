"""T-01 turn identity: replay, conflicts, serialization and recovery."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from conftest import ACCOUNT_A, customer, details
from resolve.conversation.dto import Channel
from resolve.conversation.errors import ResolveError
from resolve.conversation.identity import command_key


def test_identical_replay_returns_saved_result_without_repeating_work(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn = h.turn(conv, details())
    first = h.send(ctx, turn)
    assert h.version(conv) == 2

    again = h.send(ctx, turn)  # same body, stale expected_version: replay is checked first
    assert again == first
    assert h.version(conv) == 2
    assert h.facade.calls["create_case"] == 1
    assert h.facade.calls["investigate"] == 1
    assert h.repo.calls["complete_turn"] == 1


def test_changed_payload_with_same_turn_id_conflicts(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn_id = uuid4()
    h.send(ctx, h.turn(conv, {"type": "category_selection", "complaint_type": "BALANCE_RECHARGE"}, turn_id=turn_id))
    with pytest.raises(ResolveError) as err:
        h.send(ctx, h.turn(conv, {"type": "category_selection", "complaint_type": "CONNECTIVITY"}, turn_id=turn_id))
    assert err.value.code == "IDEMPOTENCY_CONFLICT"


def test_language_change_is_a_different_body(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn_id = uuid4()
    h.send(ctx, h.turn(conv, {"type": "text", "text": "hello"}, turn_id=turn_id))
    with pytest.raises(ResolveError) as err:
        h.send(ctx, h.turn(conv, {"type": "text", "text": "hello"}, turn_id=turn_id, language="si", version=1))
    assert err.value.code == "IDEMPOTENCY_CONFLICT"


def test_stale_version_for_a_new_turn(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, {"type": "text", "text": "hi"}))
    with pytest.raises(ResolveError) as err:
        h.send(ctx, h.turn(conv, {"type": "text", "text": "again"}, version=1))
    assert err.value.code == "STALE_VERSION"


def test_in_progress_and_busy_while_claim_is_live(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn = h.turn(conv, details())
    gate = asyncio.Event()
    original = h.facade.investigate

    async def slow_investigate(*args, **kwargs):
        await gate.wait()
        return await original(*args, **kwargs)

    h.facade.investigate = slow_investigate

    async def scenario():
        first = asyncio.create_task(h.service.handle_turn(ctx, turn))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        with pytest.raises(ResolveError) as same:
            await h.service.handle_turn(ctx, turn)
        assert same.value.code == "TURN_IN_PROGRESS" and same.value.retryable
        with pytest.raises(ResolveError) as other:
            await h.service.handle_turn(ctx, h.turn(conv, {"type": "text", "text": "hi"}, version=1))
        assert other.value.code == "CONVERSATION_BUSY"
        gate.set()
        return await first

    result = asyncio.run(scenario())
    assert result.conversation_version == 2


def test_failure_releases_claim_and_same_id_retry_does_not_duplicate_case(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn = h.turn(conv, details())
    h.facade.fail_next["investigate"] = ResolveError("DEPENDENCY_UNAVAILABLE")
    with pytest.raises(ResolveError) as err:
        h.send(ctx, turn)
    assert err.value.code == "DEPENDENCY_UNAVAILABLE"
    assert h.version(conv) == 1 and h.repo.calls["release_turn"] == 1

    result = h.send(ctx, turn)
    assert result.case_id is not None
    assert len(h.facade.cases) == 1  # create_case replayed the originating turn


def test_abandoned_claim_is_taken_over_after_lease_expiry(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn = h.turn(conv, details())
    asyncio.run(h.repo.claim_turn(ctx, conv, turn.turn_id, "abandoned-worker", 1))
    with pytest.raises(ResolveError) as err:
        h.send(ctx, h.turn(conv, {"type": "text", "text": "hi"}))
    assert err.value.code == "CONVERSATION_BUSY"

    h.clock.advance(seconds=31)
    result = h.send(ctx, turn)
    assert result.conversation_version == 2


def test_other_session_cannot_use_conversation(h) -> None:
    owner = customer(ACCOUNT_A)
    conv = h.open(owner)
    with pytest.raises(ResolveError) as err:
        h.send(customer(ACCOUNT_A), h.turn(conv, {"type": "text", "text": "hi"}))
    assert err.value.code == "RESOURCE_NOT_FOUND"


def test_channel_must_match_authenticated_context(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    with pytest.raises(ResolveError) as err:
        h.send(ctx, h.turn(conv, {"type": "text", "text": "hi"}, channel=Channel.VOICE))
    assert err.value.code == "VALIDATION_ERROR"
    assert h.repo.calls["claim_turn"] == 0


def test_retry_after_partial_progress_reuses_command_keys(h) -> None:
    """Investigation succeeded, proposal failed: the retry must not investigate again."""
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    turn = h.turn(conv, details())
    h.facade.fail_next["propose_action"] = ResolveError("DEPENDENCY_UNAVAILABLE")
    with pytest.raises(ResolveError):
        h.send(ctx, turn)

    result = h.send(ctx, turn)
    case = h.facade.cases[result.case_id]
    assert case.version == 2  # exactly one investigation revision
    assert any(card.type == "confirmation" for card in result.cards)


def test_command_keys_are_deterministic_and_distinct() -> None:
    conv, turn, case = uuid4(), uuid4(), uuid4()
    assert command_key(conv, turn, "investigate", case) == command_key(conv, turn, "investigate", case)
    assert command_key(conv, turn, "investigate", case) != command_key(conv, uuid4(), "investigate", case)
    assert command_key(conv, turn, "investigate", case) != command_key(conv, turn, "propose", case)
