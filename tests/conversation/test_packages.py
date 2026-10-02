"""Package prototype: code-ranked suggestions, a bounded agent, activation only through the confirmation card."""

from __future__ import annotations

import asyncio
import json
import pytest

from conftest import ACCOUNT_A, ACCOUNT_B, Harness, customer, guest, text
from fakes import GB_BYTES, FakeModel, FakePackagePort, extraction
from resolve.conversation.agent import PackageAgent
from resolve.conversation.dto import ActionType, Channel
from resolve.conversation.extraction import RESPONSE_SCHEMA as EXTRACTION_SCHEMA
from resolve.conversation.model import ModelError, ModelReply
from resolve.conversation.packages import facts, recommend
from resolve.conversation.rewrite import ReplyRewriter

SUGGEST = "which package is best for me?"
ACTIVATE = "activate the 25 GB one for me"
TOO_DEAR = "activate the 50 GB one"
BEST = ("Based on your 20.8 GB in the last 30 days, Synthetic 30-day 25 GB data for LKR 399.00 fits best. "
        "Synthetic 30-day 50 GB data is LKR 699.00.")


class AgentModel:
    """Scripted package agent: one response (dict, str, exception or callable) per step."""

    provider, model_name = "fake", "fake-agent"

    def __init__(self, *steps, delay: float = 0.0) -> None:
        self.steps = list(steps)
        self.payloads: list[dict] = []
        self.delay = delay

    async def generate_json(self, *, system, prompt, schema):
        payload = json.loads(prompt)
        self.payloads.append(payload)
        if self.delay:
            await asyncio.sleep(self.delay)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        step = step(payload) if callable(step) else step
        if isinstance(step, Exception):
            raise step
        body = step if isinstance(step, str) else json.dumps(step)
        return ModelReply(text=body, provider="fake", model="fake-agent", input_tokens=50, output_tokens=20)


def say(reply: str, *mentioned: str) -> dict:
    return {"tool_calls": [], "reply": reply, "mentioned_packages": list(mentioned)}


def call(*calls: tuple[str, str]) -> dict:
    return {"tool_calls": [{"tool": tool, "package_key": arg if tool == "offer_activation" else None,
                            "query": arg if tool == "search_help" else None} for tool, arg in calls],
            "reply": None, "mentioned_packages": []}


def harness(agent: AgentModel | None, with_agent: bool = True) -> Harness:
    h = Harness(model=FakeModel())
    h.service._packages = FakePackagePort(h.facade)
    h.service._package_agent = PackageAgent(agent) if with_agent and agent else None
    for message in (SUGGEST, ACTIVATE, TOO_DEAR, "deweni eka danna"):
        h.model.on(message, extraction(intent="PACKAGES"))
    h.model.on("is my package active?", extraction(intent="STATUS"))
    h.model.on("ow", extraction(intent="ACTION_DECISION", decision="ACCEPT", detected_language="si"))
    return h


def accept(h: Harness, ctx, conv, card, decision: str = "ACCEPT"):
    return h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(card.id),
                                     "proposal_hash": card.proposal_hash, "decision": decision}))


# --- ranking (code, not the model) ---------------------------------------------------


def port() -> FakePackagePort:
    return FakePackagePort(Harness().facade)


def test_cheapest_package_that_covers_last_months_use_comes_first() -> None:
    p = port()
    ranked = recommend(p.usage, p.packages, balance_minor=42000)
    assert [r.package.name for r in ranked] == [
        "Synthetic 30-day 25 GB data", "Synthetic 30-day 50 GB data", "Synthetic 7-day 6 GB data"]
    best, bigger, weekly = ranked
    assert best.covers_usage and best.affordable_now and best.reload_needed_minor == 0
    assert not bigger.affordable_now and bigger.reload_needed_minor == 27900
    assert weekly.purchases_per_month == 5 and weekly.cost_per_month_minor == 99500


def test_when_nothing_covers_the_use_the_largest_comes_first() -> None:
    p = port()
    heavy = p.usage.model_copy(update={"data_used_bytes": 80 * GB_BYTES})
    ranked = recommend(heavy, p.packages, balance_minor=None)
    assert [r.package.name for r in ranked] == [
        "Synthetic 30-day 50 GB data", "Synthetic 7-day 6 GB data", "Synthetic 1-day 1 GB data"]
    assert all(not r.covers_usage for r in ranked) and all(r.reload_needed_minor == 0 for r in ranked)


def test_facts_are_display_ready() -> None:
    p = port()
    ranked = recommend(p.usage, p.packages, 42000)
    keys = {pkg.id: f"P{i + 1}" for i, pkg in enumerate(p.packages)}
    shown = facts(p.usage, p.packages, ranked, keys, 42000, None)
    assert shown["usage"]["data_used"] == "20.8 GB" and shown["usage"]["average_per_day"] == "0.7 GB"
    assert shown["usage"]["charged_outside_a_package"] == "LKR 80.00"
    assert {"key": "P4", "price": "LKR 399.00", "data": "25 GB", "validity": "30 days"}.items() <= shown["recommendations"][0].items()
    assert shown["recommendations"][1]["reload_needed_first"] == "LKR 279.00"


# --- the agent ---------------------------------------------------------------------


def test_suggestion_reply_uses_only_given_facts() -> None:
    h = harness(AgentModel(say(BEST, "P4", "P5")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text == BEST and result.cards == [] and result.pending_question is None
    assert [p.name for p in h.state(conv).packages_shown] == ["Synthetic 30-day 25 GB data", "Synthetic 30-day 50 GB data"]
    assert [r.purpose for r in h.telemetry.records] == ["EXTRACTION", "PACKAGE_AGENT"]
    assert h.repo.source_texts[result.message_id].startswith("In the last 30 days you used 20.8 GB of data.")


def test_agent_sees_usage_balance_and_ranked_suggestions() -> None:
    model = AgentModel(say(BEST, "P4"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(SUGGEST)))
    payload = model.payloads[0]
    assert payload["message"] == SUGGEST and payload["facts"]["main_balance"] == "LKR 420.00"
    assert payload["facts"]["recommendations"][0]["key"] == "P4" and payload["tool_calls_allowed"] is True


@pytest.mark.parametrize(
    "bad",
    ["The 25 GB package is only LKR 249.00 today!", "Dial #121# to activate it.", "Activate it at hutchdeals.lk",
     "You can get 25 GB for LKR 39k."],
)
def test_invented_prices_codes_or_sites_fall_back_to_the_code_reply(bad: str) -> None:
    h = harness(AgentModel(say(bad, "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.startswith("In the last 30 days you used 20.8 GB of data. The best fit is Synthetic 30-day 25 GB data")
    assert result.reply_text.endswith("Tell me which one you'd like and I'll prepare it for you to confirm.")
    assert result.cards == []
    assert h.telemetry.records[-1].outcome == "NOT_GROUNDED"


def test_customer_choice_creates_an_offer_card_but_activates_nothing() -> None:
    model = AgentModel(call(("offer_activation", "P4")),
                       say("Tap 'Yes, go ahead' on the card to activate Synthetic 30-day 25 GB data for LKR 399.00.", "P4"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(ACTIVATE)))
    card = result.cards[0].data
    assert card.action_type is ActionType.ACTIVATE_PACKAGE and card.target_label == "Synthetic 30-day 25 GB data"
    assert "LKR 399.00 is taken from your main balance (LKR 420.00 now, LKR 21.00 after)" in card.consequences
    assert result.pending_question.code == "CONFIRM_ACTION" and h.state(conv).pending_proposal.proposal_id == card.id
    assert model.payloads[1]["tool_results"][0]["result"]["ok"] is True
    assert h.facade.calls["confirm_action"] == 0 and h.facade.balance_delta[ACCOUNT_A] == 0


def test_typed_yes_never_activates_a_package() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(ACTIVATE)))
    result = h.send(ctx, h.turn(conv, text("ow")))
    assert result.pending_question.code == "CONFIRM_ACTION" and h.facade.calls["confirm_action"] == 0


def test_accept_button_activates_and_status_reports_the_real_result() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    card = h.send(ctx, h.turn(conv, text(ACTIVATE))).cards[0].data
    accepted = accept(h, ctx, conv, card)
    assert accepted.reply_text.startswith("Activation of Synthetic 30-day 25 GB data is requested. It has not been completed yet")
    assert len(accepted.operation_ids) == 1 and h.state(conv).last_activation.package_label == "Synthetic 30-day 25 GB data"

    pending = h.send(ctx, h.turn(conv, text("is my package active?")))
    assert "has not been completed yet" in pending.reply_text and pending.cards == []

    h.facade.complete_activation(accepted.operation_ids[0], h.clock())
    done = h.send(ctx, h.turn(conv, text("is my package active?")))
    assert done.reply_text == "Your Synthetic 30-day 25 GB data activation: It has been completed and confirmed."
    account = done.cards[0].data
    assert account.balances[0].amount_minor == 2100
    assert "Synthetic 30-day 25 GB data" in [s.name for s in account.subscriptions]


def test_decline_changes_nothing() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    card = h.send(ctx, h.turn(conv, text(ACTIVATE))).cards[0].data
    result = accept(h, ctx, conv, card, "DECLINE")
    assert result.reply_text.startswith("Okay, I won't make that change.") and result.operation_ids == []
    assert h.facade.balance_delta[ACCOUNT_A] == 0 and h.state(conv).last_activation is None


def test_balance_too_low_explains_the_reload_needed() -> None:
    model = AgentModel(call(("offer_activation", "P5")),
                       say("Your balance is LKR 420.00, so please reload at least LKR 279.00 first.", "P5"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(TOO_DEAR)))
    assert result.reply_text == "Your balance is LKR 420.00, so please reload at least LKR 279.00 first."
    assert result.cards == [] and h.state(conv).pending_proposal is None
    assert model.payloads[1]["tool_results"][0]["result"]["error"] == "BALANCE_TOO_LOW"


def test_unknown_key_and_second_offer_are_refused_by_code() -> None:
    model = AgentModel(call(("offer_activation", "P9"), ("offer_activation", "P4")),
                       call(("offer_activation", "P3")), say("Tap 'Yes, go ahead' on the card.", "P4"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(ACTIVATE)))
    results = [r["result"] for r in model.payloads[2]["tool_results"]]
    assert results[0]["error"] == "UNKNOWN_PACKAGE" and results[1]["ok"] is True
    assert results[2]["error"] == "ALREADY_OFFERED_THIS_TURN"
    assert [c.data.target_label for c in result.cards] == ["Synthetic 30-day 25 GB data"]


def test_tools_cannot_run_on_the_last_step() -> None:
    model = AgentModel(call(("search_help", "how to reload")))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert len(model.payloads) == 3 and model.payloads[2]["tool_calls_allowed"] is False
    assert result.reply_text.startswith("In the last 30 days")  # no reply by the last step: code reply
    assert h.telemetry.records[-1].outcome == "INVALID_OUTPUT"


def test_failed_reply_after_an_offer_keeps_the_card_with_code_wording() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Activated! You also get a LKR 500.00 bonus.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(ACTIVATE)))
    assert result.reply_text.startswith("I can activate the package for Synthetic 30-day 25 GB data.")
    assert result.cards[0].data.action_type is ActionType.ACTIVATE_PACKAGE


@pytest.mark.parametrize("failure", [ModelError("down"), "not json"])
def test_agent_outage_gives_the_code_suggestion(failure) -> None:
    h = harness(AgentModel(failure))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.startswith("In the last 30 days you used 20.8 GB") and result.cards == []


def test_slow_agent_step_times_out_to_the_code_reply() -> None:
    h = harness(AgentModel(say(BEST), delay=1.0))
    h.service._package_agent = PackageAgent(h.service._package_agent.client, step_budget_seconds=0.2)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.startswith("In the last 30 days") and result.cards == []
    assert h.telemetry.records[-1].outcome == "TIMEOUT"


def test_agent_is_skipped_when_the_turn_deadline_is_near() -> None:
    model = AgentModel(say(BEST))
    h = harness(model)
    h.service._budgets[Channel.TEXT] = 2.0  # below MIN_AGENT_SECONDS
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert model.payloads == [] and result.reply_text.startswith("In the last 30 days") and result.cards == []


def test_without_an_agent_the_best_fit_is_offered_for_buttons_only_use() -> None:
    h = harness(None, with_agent=False)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.endswith("Tap 'Yes, go ahead' on the offer to activate it, or tell me which other package you'd like.")
    assert result.cards[0].data.target_label == "Synthetic 30-day 25 GB data"


def test_previous_list_is_given_to_the_agent_and_the_extractor() -> None:
    model = AgentModel(say(BEST, "P4", "P5"), call(("offer_activation", "P5")), say("Please reload LKR 279.00 first.", "P5"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(SUGGEST)))
    h.send(ctx, h.turn(conv, text("deweni eka danna")))
    assert model.payloads[1]["conversation"]["packages_shown_before"] == [
        {"key": "P4", "name": "Synthetic 30-day 25 GB data"}, {"key": "P5", "name": "Synthetic 30-day 50 GB data"}]
    assert json.loads(h.model.prompts[-1])["conversation"]["packages_shown"] == [
        "Synthetic 30-day 25 GB data", "Synthetic 30-day 50 GB data"]


def test_agent_reply_is_not_rewritten_again() -> None:
    from test_rewrite import RewriteModel

    h = harness(AgentModel(say(BEST, "P4")))
    rewriter = RewriteModel()
    h.service._rewriter = ReplyRewriter(rewriter)
    h.model.on(SUGGEST, extraction(intent="PACKAGES", detected_language="si"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text == BEST and rewriter.sources == []


# --- routing and scope ---------------------------------------------------------------


def test_guest_is_asked_to_sign_in() -> None:
    model = AgentModel(say(BEST))
    h = harness(model)
    ctx = guest()
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.startswith("Please sign in") and model.payloads == []


def test_without_a_package_service_it_explains_how_instead() -> None:
    h = Harness(model=FakeModel())
    h.model.on(SUGGEST, extraction(intent="PACKAGES"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert result.reply_text.startswith("HUTCH self-care publicly lists plan activation.") and result.citations
    assert h.knowledge.queries == ["activate data package"]


def test_off_topic_gets_one_polite_line_and_the_menu() -> None:
    h = Harness(model=FakeModel())
    h.model.on("who won the cricket?", extraction(intent="OFF_TOPIC"))
    for ctx in (customer(ACCOUNT_A), guest()):
        conv = h.open(ctx)
        result = h.send(ctx, h.turn(conv, text("who won the cricket?")))
        assert result.reply_text.startswith("I can only help with your HUTCH prepaid line")
        assert result.pending_question.code == "CHOOSE_COMPLAINT_TYPE"


def test_activation_offer_belongs_to_the_customer_who_got_it() -> None:
    from resolve.conversation.dto import ConfirmationRequest, Decision
    from resolve.conversation.errors import ResolveError

    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    card = h.send(ctx, h.turn(conv, text(ACTIVATE))).cards[0].data
    other = customer(ACCOUNT_B)
    request = ConfirmationRequest(proposal_hash=card.proposal_hash, decision=Decision.ACCEPT, client_turn_id=card.id)
    with pytest.raises(ResolveError) as err:
        asyncio.run(h.facade.confirm_action(other, card.id, request, "other-key"))
    assert err.value.code == "RESOURCE_NOT_FOUND"


def test_extractor_cannot_choose_package_activation_as_an_action() -> None:
    choices = EXTRACTION_SCHEMA["properties"]["action_choice"]["anyOf"][0]["enum"]
    assert "ACTIVATE_PACKAGE" not in choices and "CREATE_REVIEW_TICKET" in choices


def test_expired_activation_offer_is_refused() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    card = h.send(ctx, h.turn(conv, text(ACTIVATE))).cards[0].data
    h.clock.advance(minutes=6)
    result = accept(h, ctx, conv, card)
    assert result.operation_ids == [] and h.facade.balance_delta[ACCOUNT_A] == 0


def test_reply_written_with_a_successful_offer_saves_a_model_call() -> None:
    offer = call(("offer_activation", "P4")) | {"reply": "Tap 'Yes, go ahead' on the card to activate it.", "mentioned_packages": ["P4"]}
    model = AgentModel(offer)
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(ACTIVATE)))
    assert len(model.payloads) == 1 and result.reply_text == "Tap 'Yes, go ahead' on the card to activate it."
    assert result.cards[0].data.target_label == "Synthetic 30-day 25 GB data"


def test_reply_written_with_a_refused_offer_is_not_used() -> None:
    optimistic = call(("offer_activation", "P5")) | {"reply": "Tap 'Yes, go ahead' on the card.", "mentioned_packages": ["P5"]}
    model = AgentModel(optimistic, say("Please reload at least LKR 279.00 first.", "P5"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(TOO_DEAR)))
    assert len(model.payloads) == 2 and result.reply_text == "Please reload at least LKR 279.00 first." and result.cards == []


def test_rejected_reply_records_which_check_failed_but_not_its_text() -> None:
    h = harness(AgentModel(say("Only LKR 249.00 today!", "P4")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(SUGGEST)))
    record = h.telemetry.records[-1]
    assert (record.outcome, record.error_type) == ("NOT_GROUNDED", "NUMBERS")


def test_an_offer_reply_keeps_the_list_the_customer_is_choosing_from() -> None:
    model = AgentModel(say(BEST, "P4", "P5"),
                       call(("offer_activation", "P4")) | {"reply": "Tap 'Yes, go ahead' on the card.", "mentioned_packages": ["P4"]},
                       say("Okay.", "P4"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(SUGGEST)))
    h.send(ctx, h.turn(conv, text(ACTIVATE)))
    assert [p.name for p in h.state(conv).packages_shown] == ["Synthetic 30-day 25 GB data", "Synthetic 30-day 50 GB data"]


def test_status_reports_an_active_case_and_the_latest_activation() -> None:
    h = harness(AgentModel(call(("offer_activation", "P4")), say("Tap 'Yes, go ahead' on the card.", "P4")))
    h.model.on("mage balance eka adu wela", extraction(complaint_type="BALANCE_RECHARGE"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))  # opens case A
    card = h.send(ctx, h.turn(conv, text(ACTIVATE))).cards[-1].data
    accepted = accept(h, ctx, conv, card)
    h.facade.complete_activation(accepted.operation_ids[0], h.clock())
    result = h.send(ctx, h.turn(conv, text("is my package active?")))
    assert result.reply_text.startswith("Your balance or recharge request is open.")
    assert result.reply_text.endswith("Your Synthetic 30-day 25 GB data activation: It has been completed and confirmed.")
    assert accepted.operation_ids[0] in result.operation_ids and result.cards[-1].type == "account"


def test_singlish_ak_after_a_known_amount_is_respaced_not_rejected() -> None:
    model = AgentModel(call(("offer_activation", "P5")), say("Reload LKR 279.00k one, eta passe activate karanna puluwan.", "P5"))
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(TOO_DEAR)))
    assert result.reply_text == "Reload LKR 279.00 ak one, eta passe activate karanna puluwan."


def test_k_after_an_unknown_amount_is_still_rejected() -> None:
    h = harness(AgentModel(say("Reload LKR 300k one.", "P5")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text(SUGGEST)))
    assert h.telemetry.records[-1].error_type in {"NUMBERS", "MAGNITUDE"}
