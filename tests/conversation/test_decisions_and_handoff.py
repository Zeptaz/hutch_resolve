"""T-03: Voice consent gate, honest handoff state, FAQ scope labels, ask-again errors."""

from __future__ import annotations

from uuid import uuid4

from conftest import ACCOUNT_A, ACCOUNT_D, ACCOUNT_F, Harness, customer, details, text
from fakes import FakeModel, example, extraction
from resolve.conversation.dto import Channel, KnowledgeCard, ReceiptReference, VoiceConsentEvidence
from resolve.conversation.errors import ResolveError

YES = extraction(intent="ACTION_DECISION", decision="ACCEPT", detected_language="si")
NO = extraction(intent="ACTION_DECISION", decision="DECLINE", detected_language="si")
UNSURE = extraction(intent="ACTION_DECISION", decision="UNCLEAR")


def voice_call(h: Harness, account=ACCOUNT_A):
    ctx = customer(account, Channel.VOICE)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details(), channel=Channel.VOICE))
    proposal = next(c for c in offered.cards if c.type == "confirmation").data
    return ctx, conv, proposal


def spoken(h: Harness, conv, transcript: str, proposal=None, *, evidence_transcript=None, hash_=None):
    turn_id = uuid4()
    evidence = None
    if proposal is not None:
        evidence = VoiceConsentEvidence(
            binding_id="binding-1",
            voice_session_id="voice-1",
            final_transcript=evidence_transcript if evidence_transcript is not None else transcript,
            presented_proposal_id=proposal.id,
            presented_proposal_hash=hash_ or proposal.proposal_hash,
            turn_id=turn_id,
        )
    return h.turn(conv, text(transcript), turn_id=turn_id, channel=Channel.VOICE, voice_evidence=evidence)


def test_clear_spoken_yes_with_presentation_evidence_confirms(hm: Harness) -> None:
    hm.model.on("ow, eka nawattanna", YES)
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "ow, eka nawattanna", proposal))
    assert len(result.operation_ids) == 1
    assert "not been completed yet" in result.reply_text
    assert hm.facade.calls["confirm_action"] == 1
    assert hm.state(conv).pending_proposal is None


def test_clear_spoken_no_is_recorded_as_decline(hm: Harness) -> None:
    hm.model.on("epa", NO)
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "epa", proposal))
    assert result.operation_ids == [] and "Nothing on your account was changed" in result.reply_text
    assert hm.facade.calls["confirm_action"] == 1  # the decline itself is persisted by Resolve
    assert hm.facade.operations == {}


def test_spoken_yes_without_presentation_evidence_directs_to_buttons(hm: Harness) -> None:
    hm.model.on("yes", YES)
    ctx, conv, _ = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "yes"))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert "tap 'Yes, go ahead' or 'No, leave it'" in result.reply_text
    assert "Nothing has changed yet" in result.reply_text
    assert hm.facade.calls["confirm_action"] == 0
    assert hm.state(conv).pending_proposal is not None


def test_initial_voice_offer_does_not_invite_spoken_confirmation(hm: Harness) -> None:
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = hm.open(ctx)
    result = hm.send(ctx, hm.turn(conv, details(), channel=Channel.VOICE))
    assert "Shall I go ahead" not in result.reply_text
    assert "press 'Yes, go ahead' or 'No, leave it'" in result.reply_text
    assert "Nothing will change until you press" in result.reply_text


def test_voice_follow_up_reminds_customer_to_use_buttons(hm: Harness) -> None:
    hm.model.on("Is it still open?", extraction(intent="FOLLOW_UP"))
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "Is it still open?"))
    assert "tap 'Yes, go ahead' or 'No, leave it'" in result.reply_text
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert hm.state(conv).pending_proposal.proposal_id == proposal.id


def test_voice_call_cannot_switch_topic_while_an_offer_waits(hm: Harness) -> None:
    hm.model.on("what is my balance", extraction(intent="ACCOUNT_ENQUIRY"))
    ctx, conv, proposal = voice_call(hm)
    calls_before = dict(hm.facade.calls)
    result = hm.send(ctx, spoken(hm, conv, "what is my balance"))
    assert result.reply_text.startswith("Before we move on, please answer the offer on your screen")
    assert result.pending_question.code == "CONFIRM_ACTION" and result.cards == []
    assert hm.state(conv).pending_proposal.proposal_id == proposal.id
    assert dict(hm.facade.calls) == calls_before  # nothing else was looked up or changed


def test_expired_offer_no_longer_holds_the_call(hm: Harness) -> None:
    hm.model.on("what is my balance", extraction(intent="ACCOUNT_ENQUIRY"))
    ctx, conv, _ = voice_call(hm)
    hm.clock.advance(minutes=6)
    result = hm.send(ctx, spoken(hm, conv, "what is my balance"))
    assert "Before we move on" not in result.reply_text
    assert hm.state(conv).pending_proposal is None


def test_voice_answer_asks_anything_else_and_a_spoken_no_ends_the_call(hm: Harness) -> None:
    hm.model.on("what is my balance", extraction(intent="ACCOUNT_ENQUIRY"))
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = hm.open(ctx)
    answered = hm.send(ctx, hm.turn(conv, text("what is my balance"), channel=Channel.VOICE))
    assert answered.reply_text.endswith("Is there anything else I can help you with?")
    assert answered.pending_question.code == "ANYTHING_ELSE"
    goodbye = hm.send(ctx, hm.turn(conv, text("No, thank you."), channel=Channel.VOICE))
    assert goodbye.reply_text == "Thank you for calling HUTCH. Goodbye!"
    assert goodbye.pending_question.code == "CALL_ENDED"
    assert hm.state(conv).pending_question is None


def test_spoken_no_while_an_offer_waits_never_ends_the_call(hm: Harness) -> None:
    hm.model.on("no", NO)
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "no"))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert hm.state(conv).pending_proposal.proposal_id == proposal.id
    assert hm.facade.calls["confirm_action"] == 0


def test_text_chat_never_gets_the_voice_closing_question(hm: Harness) -> None:
    hm.model.on("what is my balance", extraction(intent="ACCOUNT_ENQUIRY"))
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    answered = hm.send(ctx, hm.turn(conv, text("what is my balance")))
    assert "anything else" not in answered.reply_text
    assert answered.pending_question is None or answered.pending_question.code != "ANYTHING_ELSE"


def test_repeated_spoken_yes_never_claims_action_or_asks_for_more_spoken_yes(hm: Harness) -> None:
    hm.model.on("yes go ahead", YES)
    ctx, conv, proposal = voice_call(hm)
    for _ in range(3):
        result = hm.send(ctx, spoken(hm, conv, "yes go ahead"))
        assert result.pending_question.code == "CONFIRM_ACTION"
        assert "tap 'Yes, go ahead' or 'No, leave it'" in result.reply_text
        assert hm.state(conv).pending_proposal.proposal_id == proposal.id
    assert hm.facade.calls["confirm_action"] == 0
    assert hm.facade.operations == {}


def test_spoken_yes_for_a_different_presentation_asks_again(hm: Harness) -> None:
    hm.model.on("yes", YES)
    ctx, conv, proposal = voice_call(hm)
    stale = hm.send(ctx, spoken(hm, conv, "yes", proposal, hash_="c" * 64))
    assert stale.pending_question.code == "CONFIRM_ACTION"
    mismatched_transcript = hm.send(ctx, spoken(hm, conv, "yes", proposal, evidence_transcript="no"))
    assert mismatched_transcript.pending_question.code == "CONFIRM_ACTION"
    assert hm.facade.calls["confirm_action"] == 0


def test_unclear_spoken_answer_asks_again(hm: Harness) -> None:
    hm.model.on("yes but what about my old charges?", UNSURE)
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "yes but what about my old charges?", proposal))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert hm.facade.calls["confirm_action"] == 0
    assert hm.state(conv).pending_proposal is not None


def test_typed_clear_yes_in_text_chat_still_needs_the_button(hm: Harness) -> None:
    hm.model.on("ow", YES)
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, details()))
    result = hm.send(ctx, hm.turn(conv, text("ow")))
    assert "buttons on the offer" in result.reply_text
    assert hm.facade.calls["confirm_action"] == 0


def test_model_outage_during_voice_decision_asks_again(hm: Harness) -> None:
    from resolve.conversation.model import ModelError

    hm.model.on("ow", ModelError("unavailable"))
    ctx, conv, proposal = voice_call(hm)
    result = hm.send(ctx, spoken(hm, conv, "ow", proposal))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert hm.facade.calls["confirm_action"] == 0


def test_resolve_confirmation_required_keeps_offer_open(hm: Harness) -> None:
    hm.model.on("ow", YES)
    ctx, conv, proposal = voice_call(hm)
    hm.facade.fail_next["confirm_action"] = ResolveError("CONFIRMATION_REQUIRED")
    result = hm.send(ctx, spoken(hm, conv, "ow", proposal))
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert hm.state(conv).pending_proposal.proposal_id == proposal.id


def test_review_ticket_acceptance_never_invents_a_ticket_number(h: Harness) -> None:
    ctx = customer(ACCOUNT_D)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details()))
    proposal = next(c for c in offered.cards if c.type == "confirmation").data
    assert proposal.action_type == "CREATE_REVIEW_TICKET"
    result = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(proposal.id), "proposal_hash": proposal.proposal_hash, "decision": "ACCEPT"}))
    operation_id = result.operation_ids[0]
    assert f"request ID {operation_id}" in result.reply_text
    assert "Ticket number" not in result.reply_text


def test_status_shows_pending_handoff_during_crm_outage(hm: Harness) -> None:
    hm.model.on("did it reach the team?", extraction(intent="STATUS"))
    ctx = customer(ACCOUNT_D)
    conv = hm.open(ctx)
    offered = hm.send(ctx, hm.turn(conv, details()))
    case_id = offered.case_id
    hm.facade.handoffs[case_id] = example("crm_outage_handoff")
    case = hm.facade.cases[case_id]
    hm.facade.cases[case_id] = case.model_copy(update={"receipt": ReceiptReference(id=example("receipt")["id"], revision=1)})

    result = hm.send(ctx, hm.turn(conv, text("did it reach the team?")))
    assert "still pending; no ticket number has been issued yet" in result.reply_text
    assert "Ticket number:" not in result.reply_text
    assert [c.type for c in result.cards] == ["ticket", "receipt"]
    assert result.cards[0].data.provider_ticket_id is None


def test_status_mentions_ticket_number_only_when_issued(hm: Harness) -> None:
    hm.model.on("status?", extraction(intent="STATUS"))
    ctx = customer(ACCOUNT_D)
    conv = hm.open(ctx)
    case_id = hm.send(ctx, hm.turn(conv, details())).case_id
    hm.facade.handoffs[case_id] = example("crm_outage_handoff") | {"delivery_state": "DELIVERED", "provider_ticket_id": "SYN-TKT-42"}
    hm.facade.cases[case_id] = hm.facade.cases[case_id].model_copy(update={"receipt": ReceiptReference(id=example("receipt")["id"], revision=1)})
    result = hm.send(ctx, hm.turn(conv, text("status?")))
    assert "Ticket number: SYN-TKT-42." in result.reply_text


def test_synthetic_knowledge_is_labelled_as_demo_policy(hm: Harness) -> None:
    hm.knowledge.cards.append(
        KnowledgeCard(
            article_id=uuid4(), article_key="demo-retention", language="en", title="Demo retention",
            content="Synthetic demo sessions expire after seven days.", url="https://example.invalid/demo",
            reviewed_at="2026-10-02", version=1, scope="SYNTHETIC",
        )
    )
    hm.model.on("how long is demo retention", extraction(intent="FAQ", faq_query="demo retention"))
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    result = hm.send(ctx, hm.turn(conv, text("how long is demo retention")))
    assert result.reply_text.startswith("This is a demo policy for the simulation, not an official HUTCH rule:")
    assert result.citations[0].scope == "SYNTHETIC"


def test_conflicting_evidence_still_allows_human_review(hm: Harness) -> None:
    hm.model.on("I want a person", extraction(intent="HUMAN_REQUEST"))
    ctx = customer(ACCOUNT_D)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, details()))
    result = hm.send(ctx, hm.turn(conv, text("I want a person")))
    assert next(c for c in result.cards if c.type == "confirmation").data.action_type == "CREATE_REVIEW_TICKET"
    assert hm.facade.escalation_reasons == ["Customer asked for a person to review this case."]


def test_follow_up_offer_invalidated_by_the_finished_first_action_is_offered_again(h: Harness) -> None:
    """F lists VAS stop then review. The review is offered while the VAS stop is still running; when
    that finishes, Resolve invalidates the review proposal. Accepting it must not dead-end."""
    ctx = customer(ACCOUNT_F)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, details("VAS_DISPUTE")))
    first = next(c for c in offered.cards if c.type == "confirmation").data
    assert first.action_type == "DEACTIVATE_VAS"
    accepted = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(first.id),
                                         "proposal_hash": first.proposal_hash, "decision": "ACCEPT"}))
    follow_up = next(c for c in accepted.cards if c.type == "confirmation").data
    assert follow_up.action_type == "CREATE_REVIEW_TICKET"

    h.facade.fail_next["confirm_action"] = ResolveError("PROPOSAL_INVALIDATED")
    refreshed = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(follow_up.id),
                                          "proposal_hash": follow_up.proposal_hash, "decision": "ACCEPT"}))
    again = next(c for c in refreshed.cards if c.type == "confirmation").data
    assert again.action_type == "CREATE_REVIEW_TICKET" and again.id != follow_up.id
    assert refreshed.reply_text.startswith("Things changed since that offer was made")
    assert refreshed.pending_question.code == "CONFIRM_ACTION"

    done = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(again.id),
                                     "proposal_hash": again.proposal_hash, "decision": "ACCEPT"}))
    assert done.operation_ids


def test_declining_a_requested_review_does_not_offer_the_same_review_again(hm: Harness) -> None:
    """F lists the VAS stop, then the review. If the customer asks for a person while the review is
    still a listed option and then declines it, the same review must not come straight back."""
    hm.model.on("I want a person", extraction(intent="HUMAN_REQUEST"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    offered = hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    assert next(c for c in offered.cards if c.type == "confirmation").data.action_type == "DEACTIVATE_VAS"
    assert [c.action_type for c in hm.state(conv).pending_choices] == ["CREATE_REVIEW_TICKET"]
    asked = hm.send(ctx, hm.turn(conv, text("I want a person")))
    review = next(c for c in asked.cards if c.type == "confirmation").data
    assert review.action_type == "CREATE_REVIEW_TICKET"
    declined = hm.send(ctx, hm.turn(conv, {"type": "action_decision", "proposal_id": str(review.id),
                                           "proposal_hash": review.proposal_hash, "decision": "DECLINE"}))
    assert not [c for c in declined.cards if c.type == "confirmation"]
    assert declined.pending_question is None
