"""CB-003: questions about the customer's own services/packages get an answer from the account view.

"Mata VAS charges monadwada kiyanna puluwanda?" used to end at "I don't have reviewed information".
"""

from __future__ import annotations

import json

from conftest import ACCOUNT_A, Harness, customer, text
from fakes import FakeModel, extraction
from resolve.conversation.model import ModelReply
from resolve.conversation.rewrite import ReplyRewriter

SERVICES = extraction(intent="ACCOUNT_ENQUIRY", account_topic="SERVICES", detected_language="si", script="LATIN")
PACKAGES = extraction(intent="ACCOUNT_ENQUIRY", account_topic="PACKAGES")
YES = extraction(intent="ACTION_DECISION", decision="ACCEPT", detected_language="si", script="LATIN")
NO = extraction(intent="ACTION_DECISION", decision="DECLINE", detected_language="si", script="LATIN")
UNSURE = extraction(intent="ACTION_DECISION", decision="UNCLEAR", detected_language="si", script="LATIN")
QUESTION = "Mata VAS charges monadwada kiyanna puluwanda ?"


def _services_turn(h: Harness):
    h.model.on(QUESTION, SERVICES)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    return ctx, conv, h.send(ctx, h.turn(conv, text(QUESTION)))


def test_services_question_lists_value_added_services_and_offers_a_charge_check() -> None:
    h = Harness(model=FakeModel())
    _, conv, result = _services_turn(h)
    assert "Synthetic video alerts (active, renews automatically)" in result.reply_text
    assert "LKR" not in result.reply_text  # no price is invented: the account view has none
    assert result.pending_question.code == "OFFER_CHARGE_CHECK"
    assert any(card.type == "account" for card in result.cards)
    assert h.facade.calls["create_case"] == 0  # a question is not a complaint
    assert h.state(conv).candidate.complaint_type == "VAS_DISPUTE"


def test_yes_to_the_charge_check_investigates_the_service_charges() -> None:
    h = Harness(model=FakeModel())
    ctx, conv, _ = _services_turn(h)
    h.model.on("ow", YES)
    result = h.send(ctx, h.turn(conv, text("ow")))
    assert h.facade.calls["create_case"] == 1 and h.facade.calls["investigate"] == 1
    assert "service charge records" in result.reply_text
    assert h.state(conv).candidate is None and h.state(conv).active_case_id is not None


def test_no_to_the_charge_check_changes_nothing() -> None:
    h = Harness(model=FakeModel())
    ctx, conv, _ = _services_turn(h)
    h.model.on("epa", NO)
    result = h.send(ctx, h.turn(conv, text("epa")))
    assert h.facade.calls["create_case"] == 0
    assert result.pending_question.code == "CHOOSE_COMPLAINT_TYPE" and h.state(conv).candidate is None


def test_unclear_answer_asks_the_charge_check_again() -> None:
    h = Harness(model=FakeModel())
    ctx, conv, _ = _services_turn(h)
    h.model.on("hmm", UNSURE)
    result = h.send(ctx, h.turn(conv, text("hmm")))
    assert h.facade.calls["create_case"] == 0 and result.pending_question.code == "OFFER_CHARGE_CHECK"


def test_packages_question_lists_packages_not_the_balance() -> None:
    h = Harness(model=FakeModel())
    h.model.on("what packages do I have", PACKAGES)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("what packages do I have")))
    assert result.reply_text.startswith(("Packages on your line:", "You have no packages"))
    assert "balance is" not in result.reply_text


def test_balance_question_is_unchanged() -> None:
    h = Harness(model=FakeModel())
    h.model.on("what is my balance", extraction(intent="ACCOUNT_ENQUIRY", account_topic="BALANCE"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("what is my balance")))
    assert result.reply_text.startswith("Your main balance is LKR ")


class _Singlish:
    provider, model_name = "fake", "fake-rewriter"

    def __init__(self) -> None:
        self.sources: list[str] = []

    async def generate_json(self, *, system, prompt, schema):
        english = json.loads(prompt)["reply_en"]
        self.sources.append(english)
        return ModelReply(text=json.dumps({"reply": f"[si] {english}"}), provider="fake", model="fake-rewriter")


def test_simple_prompts_are_still_put_in_the_customers_style_after_a_case_exists() -> None:
    h = Harness(model=FakeModel())
    rewriter = _Singlish()
    h.service._rewriter = ReplyRewriter(rewriter)
    h.model.on("mage balance eka adu wela", extraction(complaint_type="BALANCE_RECHARGE", detected_language="si", script="LATIN"))
    h.model.on("weather eka kohomada", extraction(intent="OFF_TOPIC", detected_language="si", script="LATIN"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert h.state(conv).active_case_id is not None
    result = h.send(ctx, h.turn(conv, text("weather eka kohomada")))
    assert result.reply_text.startswith("[si] I can only help with your HUTCH prepaid line")


def test_consent_prompt_is_never_rewritten() -> None:
    from resolve.conversation.service import _confirm_prompt
    from resolve.conversation.state import DialogueState

    draft, _ = _confirm_prompt(DialogueState(language="si", script="LATIN"))
    assert draft.rewrite_allowed is False


def test_reviewed_locale_prompt_is_not_rewritten_again(monkeypatch) -> None:
    from resolve.conversation import templates as t
    from resolve.conversation.service import _ask
    from resolve.conversation.state import DialogueState

    draft_locale = t.load_locale("si-Latn")
    monkeypatch.setattr(t, "load_locale", lambda language: draft_locale | {"status": t.REVIEWED} if str(language) == "si-Latn" else {})
    draft, _ = _ask(DialogueState(language="si", script="LATIN"), "X", "prompt", ["text"])
    assert draft.rewrite_allowed is False


def _vas_result(terms):
    from uuid import uuid4

    from fakes import scenario_result
    from resolve.conversation.dto import Calculation, InvestigationResult

    result = InvestigationResult.model_validate(scenario_result("F"))
    calc = Calculation(code="BALANCE_LEDGER_RECONCILIATION", unit="LKR_MINOR", opening=0, expected=0, observed=0, delta=0,
                       evidence_ids=[], terms=[{"evidence_id": uuid4(), "label": label, "value": value} for label, value in terms])
    return result.model_copy(update={"calculations": [calc]})


def test_vas_investigation_states_each_posted_charge_line_verbatim() -> None:
    from resolve.conversation.service import _investigation_draft

    inv = _vas_result([("RECHARGE", 100_000), ("VAS_CHARGE", -6000), ("RATED_USAGE", -2100), ("VAS_CHARGE", -6000)])
    draft = _investigation_draft(inv, None, "en")
    assert draft.reply_text.startswith("Value-added service charges posted in these records: -LKR 60.00, -LKR 60.00.")


def test_vas_investigation_without_charge_lines_adds_nothing() -> None:
    from resolve.conversation.service import _investigation_draft

    draft = _investigation_draft(_vas_result([("RECHARGE", 100_000)]), None, "en")
    assert "Value-added service charges posted" not in draft.reply_text


def test_yes_answers_the_charge_check_even_while_an_older_offer_is_open() -> None:
    h = Harness(model=FakeModel())
    h.model.on("mage balance eka adu wela", extraction(complaint_type="BALANCE_RECHARGE", detected_language="si", script="LATIN"))
    h.model.on(QUESTION, SERVICES)
    h.model.on("ow", YES)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert h.state(conv).pending_proposal is not None  # an offer is open on its card
    h.send(ctx, h.turn(conv, text(QUESTION)))
    result = h.send(ctx, h.turn(conv, text("ow")))
    assert h.facade.calls["create_case"] == 2 and h.facade.calls["confirm_action"] == 0  # looked up, nothing accepted
    assert "service charge records" in result.reply_text
