"""Structured (model-free) paths through ResolveFacade for scenarios A, D and partial evidence."""

from __future__ import annotations

from datetime import timedelta

from conftest import ACCOUNT_A, ACCOUNT_D, ACCOUNT_PARTIAL, SIM_START, customer, details, guest
from resolve.conversation.dto import Channel, VoiceConsentEvidence
from resolve.conversation.errors import ResolveError


def decision(proposal_card, value: str) -> dict:
    return {
        "type": "action_decision",
        "proposal_id": str(proposal_card.data.id),
        "proposal_hash": proposal_card.data.proposal_hash,
        "decision": value,
    }


def confirmation_card(result):
    return next(card for card in result.cards if card.type == "confirmation")


def test_category_selection_asks_for_details(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "category_selection", "complaint_type": "BALANCE_RECHARGE"}))
    assert result.pending_question.code == "COMPLAINT_DETAILS"
    assert "complaint_details" in result.pending_question.allowed_input_types
    assert h.state(conv).candidate.complaint_type == "BALANCE_RECHARGE"
    assert h.facade.calls["create_case"] == 0


def test_a_reconciles_and_offers_vas_proposal_from_resolve(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details(amount_minor=100000)))

    # Reply text carries Resolve's finding verbatim; nothing is recomputed here.
    assert result.reply_text.startswith(
        "I looked at your balance or recharge records for 2 Oct, 08:00–12:00. You mentioned LKR 1,000.00. "
        "The posted recharge and subsequent deductions reconcile to LKR 420."
    )
    kinds = [card.type for card in result.cards]
    assert kinds == ["calculation", "finding", "confirmation"]
    calc = result.cards[0].data
    assert (calc.expected, calc.observed, calc.delta) == (42000, 42000, 0)

    proposal = confirmation_card(result).data
    assert proposal.action_type == "DEACTIVATE_VAS"
    assert result.pending_question.code == "CONFIRM_ACTION"
    state = h.state(conv)
    assert state.active_case_id == result.case_id
    assert state.pending_proposal.proposal_id == proposal.id
    assert state.candidate is None


def test_accept_returns_pending_operation_never_success(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details()))
    accept = h.turn(conv, decision(confirmation_card(offered), "ACCEPT"))
    result = h.send(ctx, accept)

    assert len(result.operation_ids) == 1
    assert h.facade.operations[result.operation_ids[0]].status == "PENDING"
    assert "not been completed yet" in result.reply_text
    assert "completed and confirmed" not in result.reply_text
    assert h.state(conv).pending_proposal is None

    # Retried click: replayed result, still exactly one confirmation and one operation.
    assert h.send(ctx, accept) == result
    assert h.facade.calls["confirm_action"] == 1
    assert len(h.facade.operations) == 1


def test_decline_changes_nothing(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details()))
    result = h.send(ctx, h.turn(conv, decision(confirmation_card(offered), "DECLINE")))
    assert result.operation_ids == []
    assert "Nothing on your account was changed" in result.reply_text
    assert h.facade.operations == {}
    assert h.facade.calls["confirm_action"] == 1  # decline is still recorded by Resolve


def test_d_conflict_blocks_conclusion_and_offers_only_review(h) -> None:
    ctx = customer(ACCOUNT_D)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details()))

    assert "differs by LKR 70" in result.reply_text
    assert "can't give a final answer or change your account" in result.reply_text
    proposal = confirmation_card(result).data
    assert proposal.action_type == "CREATE_REVIEW_TICKET"
    calc = result.cards[0].data
    assert (calc.expected, calc.observed, calc.delta) == (42000, 35000, -7000)


def test_partial_evidence_names_missing_records_and_offers_nothing(h) -> None:
    ctx = customer(ACCOUNT_PARTIAL)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details()))
    assert "the starting balance" in result.reply_text
    assert all(card.type != "confirmation" for card in result.cards)
    assert result.pending_question is None
    assert h.state(conv).pending_proposal is None
    assert h.facade.calls["propose_action"] == 0


def test_each_complaint_opens_its_own_case(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    first = h.send(ctx, h.turn(conv, details()))
    second = h.send(ctx, h.turn(conv, details("VAS_DISPUTE")))
    assert first.case_id != second.case_id
    assert h.state(conv).active_case_id == second.case_id


def test_invalid_windows_ask_again_without_calling_resolve(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    backwards = h.send(ctx, h.turn(conv, details(start=SIM_START, end=SIM_START - timedelta(hours=1))))
    assert backwards.pending_question.code == "COMPLAINT_DETAILS"
    assert "must be after the start" in backwards.reply_text
    too_long = h.send(ctx, h.turn(conv, details(start=SIM_START - timedelta(days=31), end=SIM_START)))
    assert "up to 30 days" in too_long.reply_text
    assert h.facade.calls["create_case"] == 0


def test_guest_is_asked_to_sign_in_for_account_paths(h) -> None:
    ctx = guest()
    conv = h.open(ctx)
    for payload in (
        {"type": "category_selection", "complaint_type": "DATA_DEPLETION"},
        details(),
        {"type": "text", "text": "my balance is wrong"},
    ):
        result = h.send(ctx, h.turn(conv, payload))
        assert result.pending_question.code == "LOGIN_REQUIRED"
    assert sum(h.facade.calls.values()) == 0


def test_free_text_without_model_offers_category_form(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "mage balance eka adu wela"}))
    assert result.pending_question.code == "CHOOSE_COMPLAINT_TYPE"
    assert result.pending_question.allowed_input_types == ["category_selection", "text"]


def test_plain_text_yes_never_confirms(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, details()))
    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "yes do it"}))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert h.facade.calls["confirm_action"] == 0
    assert h.state(conv).pending_proposal is not None


def test_decision_for_a_proposal_not_presented_here_is_refused(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details()))
    card = confirmation_card(offered)
    forged = decision(card, "ACCEPT") | {"proposal_hash": "b" * 64}
    result = h.send(ctx, h.turn(conv, forged))
    assert "no longer the one I'm waiting on" in result.reply_text
    assert h.facade.calls["confirm_action"] == 0


def test_expired_proposal_reported_by_resolve(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details()))
    h.clock.advance(minutes=6)
    result = h.send(ctx, h.turn(conv, decision(confirmation_card(offered), "ACCEPT")))
    assert "expired" in result.reply_text and result.operation_ids == []
    assert h.state(conv).pending_proposal is None
    assert h.facade.operations == {}


def test_voice_decision_requires_matching_presentation_evidence(h) -> None:
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details(), channel=Channel.VOICE))
    card = confirmation_card(offered)

    no_evidence = h.send(ctx, h.turn(conv, decision(card, "ACCEPT"), channel=Channel.VOICE))
    assert no_evidence.pending_question.code == "CONFIRM_ACTION"
    assert h.facade.calls["confirm_action"] == 0

    turn_id = __import__("uuid").uuid4()
    evidence = VoiceConsentEvidence(
        binding_id="binding-1",
        voice_session_id="voice-1",
        final_transcript="ow, eka nawattanna",
        presented_proposal_id=card.data.id,
        presented_proposal_hash=card.data.proposal_hash,
        turn_id=turn_id,
    )
    accepted = h.send(
        ctx, h.turn(conv, decision(card, "ACCEPT"), turn_id=turn_id, channel=Channel.VOICE, voice_evidence=evidence)
    )
    assert len(accepted.operation_ids) == 1


def test_case_selection_switches_active_case_within_conversation(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    first = h.send(ctx, h.turn(conv, details()))
    h.send(ctx, h.turn(conv, details("VAS_DISPUTE")))
    result = h.send(ctx, h.turn(conv, {"type": "case_selection", "case_id": str(first.case_id)}))
    assert result.case_id == first.case_id
    assert h.state(conv).active_case_id == first.case_id
    assert h.state(conv).pending_proposal is None  # the pending offer belonged to the other case


def test_case_from_another_account_is_not_found(h) -> None:
    other = customer(ACCOUNT_D)
    other_conv = h.open(other)
    foreign = h.send(other, h.turn(other_conv, details()))

    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "case_selection", "case_id": str(foreign.case_id)}))
    assert "couldn't find that case" in result.reply_text
    assert h.state(conv).active_case_id is None


def test_unexpected_facade_errors_propagate_for_api_layer(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.facade.fail_next["create_case"] = ResolveError("STALE_VERSION")
    try:
        h.send(ctx, h.turn(conv, details()))
    except ResolveError as err:
        assert err.code == "STALE_VERSION" and err.http_status == 409
    else:
        raise AssertionError("expected STALE_VERSION")


def test_history_shows_readable_text_for_buttons_and_forms(h) -> None:
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, {"type": "category_selection", "complaint_type": "BALANCE_RECHARGE"}))
    offered = h.send(ctx, h.turn(conv, details()))
    h.send(ctx, h.turn(conv, decision(confirmation_card(offered), "ACCEPT")))
    user_bodies = [m.body for m in h.repo.conversations[conv].messages if m.speaker == "USER"]
    assert user_bodies == ["Balance or recharge", "Balance or recharge: details sent", "Yes, go ahead."]
    assert not any("{" in body or "proposal" in body for body in user_bodies)
