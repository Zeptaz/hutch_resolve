"""How-to answers: written by the model from knowledge cards only, checked by code before use."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest

from conftest import ACCOUNT_A, Harness, customer, guest, text
from fakes import FakeModel, extraction
from resolve.conversation.answer import GroundedAnswerer, grounded
from resolve.conversation.dto import Channel, KnowledgeCard, Language
from resolve.conversation.errors import ResolveError
from resolve.conversation.extraction import Script
from resolve.conversation.model import ModelError, ModelReply

DRAFTS = Path(__file__).resolve().parents[2] / "backend/resolve/conversation/knowledge/hutch_public_drafts.json"
RELOAD_SI = "hi mata reload ekak danna one"


def draft_cards() -> list[KnowledgeCard]:
    data = json.loads(DRAFTS.read_text(encoding="utf-8"))
    return [
        KnowledgeCard(article_id=UUID(int=0x91000000_0000_4000_8000_000000000100 + i), article_key=d["article_key"],
                      language="en", title=d["title"], content=d["content"], url=d["url"],
                      reviewed_at=data["fetched_at"], version=1, scope="PUBLIC")
        for i, d in enumerate(data["cards"])
    ]


class KeyedKnowledge:
    """Returns cards by article key for a query, so a test controls exactly what was retrieved."""

    def __init__(self, routes: dict[str, list[str]]) -> None:
        self.by_key = {c.article_key: c for c in draft_cards()}
        self.routes = routes

    async def search(self, ctx, query, language, limit=3):
        return [self.by_key[k] for k in self.routes.get(query, [])][:limit]


class AnswerModel:
    """Scripted answer writer; records what it was given."""

    provider, model_name = "fake", "fake-answerer"

    def __init__(self, respond=None, delay: float = 0.0) -> None:
        self.respond = respond or (lambda payload: {"answer": "Use reloadpay.hutch.lk.", "used_articles": ["how-to-reload"]})
        self.delay = delay
        self.payloads: list[dict] = []

    async def generate_json(self, *, system, prompt, schema):
        payload = json.loads(prompt)
        self.payloads.append(payload)
        if self.delay:
            await asyncio.sleep(self.delay)
        result = self.respond(payload)
        if isinstance(result, Exception):
            raise result
        body = result if isinstance(result, str) else json.dumps(result)
        return ModelReply(text=body, provider="fake", model="fake-answerer", input_tokens=40, output_tokens=30)


def harness(answer_model: AnswerModel, routes: dict[str, list[str]] | None = None) -> Harness:
    h = Harness(model=FakeModel())
    h.service._knowledge = KeyedKnowledge(routes or {"how to reload": ["how-to-reload", "self-care-app"]})
    h.service._answerer = GroundedAnswerer(answer_model)
    h.model.on(RELOAD_SI, extraction(intent="FAQ", detected_language="si", script="LATIN", faq_query="how to reload"))
    return h


# --- the code check -------------------------------------------------------------

RELOAD = next(c for c in draft_cards() if c.article_key == "how-to-reload").content


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("reloadpay.hutch.lk eken LKR 50 idan LKR 50,000 dakwa reload karanna puluwan.", True),
        ("Visa, Mastercard or Amex cards work; international cards can use ding.com.", True),
        ("Call 0112 345 678 to reload.", False),  # invented phone number
        ("Go to hutchpay.lk to reload.", False),  # invented site
        ("Email reloads@hutch.lk for help.", False),  # invented e-mail
        ("Open https://reloadpay.hutch.lk now.", False),  # link where the card has none
        ("You can reload up to LKR 50k.", False),  # reads as a magnitude
        ("Dial #123# to reload.", False),  # invented USSD code
        ("1. Open reloadpay.hutch.lk\n2. Enter the number and amount\n3) Pay by Visa or Mastercard", True),  # step numbers
        ("1. Open reloadpay.hutch.lk\n2. Pay LKR 100", False),  # a step can't carry a new amount
        ("Reload in 3 steps on reloadpay.hutch.lk.", False),  # only line-start markers are layout
        ("70) Open reloadpay.hutch.lk", False),  # not a step marker
        ("reload " * 300, False),  # too long
    ],
)
def test_answer_may_only_use_facts_from_the_cards(answer: str, ok: bool) -> None:
    assert grounded(answer, RELOAD) is ok


def test_contacts_on_the_support_card_are_allowed() -> None:
    support = next(c for c in draft_cards() if c.article_key == "contact-support").content
    assert grounded("Call 1788 (24x7) or WhatsApp 0788 777 111, or e-mail cs@hutchison.lk.", support)


def test_draft_cards_are_marked_unreviewed_and_cite_hutch_pages() -> None:
    data = json.loads(DRAFTS.read_text(encoding="utf-8"))
    assert data["status"] == "DRAFT_FOR_TEAM_REVIEW"
    keys = [c["article_key"] for c in data["cards"]]
    assert len(keys) == len(set(keys))
    for card in data["cards"]:
        assert card["url"].startswith("https://hutch.lk/") and card["aliases"] and len(card["content"]) < 1500


# --- the answerer ---------------------------------------------------------------


def run(model: AnswerModel, cards=None, **kwargs):
    answerer = GroundedAnswerer(model, budget_seconds=kwargs.pop("budget", 6.0))
    return asyncio.run(answerer.answer(kwargs.pop("question", RELOAD_SI), cards or draft_cards()[:2],
                                       kwargs.pop("language", Language.SI), kwargs.pop("script", Script.LATIN), **kwargs))


def test_answer_uses_only_the_cards_it_names_as_citations() -> None:
    outcome = run(AnswerModel(lambda p: {"answer": "Hutch Self-Care app eken reload karanna.",
                                        "used_articles": ["self-care-app", "self-care-app"]}))
    assert outcome.failure is None and outcome.text == "Hutch Self-Care app eken reload karanna."
    assert [c.article_key for c in outcome.used] == ["self-care-app"]


def test_question_style_and_articles_are_sent_as_data() -> None:
    model = AnswerModel()
    run(model, account_fact="The customer's current main balance is LKR 420.00 (as of 2 Oct, 12:00).")
    payload = model.payloads[0]
    assert payload["question"] == RELOAD_SI and "Singlish" in payload["target_style"]
    assert [a["article_key"] for a in payload["articles"]] == ["how-to-reload", "self-care-app"]
    assert "LKR 420.00" in payload["account_fact"]


def test_english_and_tamil_styles() -> None:
    assert GroundedAnswerer.style_for(Language.EN, Script.LATIN) == "clear, simple English"
    assert GroundedAnswerer.style_for(Language.TA, None) == "Tamil in Tamil script"


@pytest.mark.parametrize(
    ("respond", "failure"),
    [
        (lambda p: {"answer": "Use reloadpay.hutch.lk.", "used_articles": ["made-up-article"]}, "NOT_GROUNDED"),
        (lambda p: {"answer": "Use reloadpay.hutch.lk.", "used_articles": []}, "NOT_GROUNDED"),
        (lambda p: {"answer": "Reload at hutchpay.lk.", "used_articles": ["how-to-reload"]}, "NOT_GROUNDED"),
        (lambda p: "not json", "INVALID_OUTPUT"),
        (lambda p: {"answer": "", "used_articles": ["how-to-reload"]}, "INVALID_OUTPUT"),
        (lambda p: ModelError("unavailable"), "MODEL_ERROR"),
    ],
)
def test_bad_answers_are_rejected(respond, failure: str) -> None:
    outcome = run(AnswerModel(respond))
    assert outcome.text is None and outcome.used == [] and outcome.failure == failure


def test_slow_answer_times_out_within_the_budget() -> None:
    outcome = run(AnswerModel(delay=1.0), budget=0.1)
    assert outcome.failure == "TIMEOUT" and outcome.latency_ms < 900


# --- in the conversation ----------------------------------------------------------


def test_singlish_reload_request_gets_how_to_steps_with_balance() -> None:
    model = AnswerModel(lambda p: {
        "answer": "Oyage balance eka LKR 420.00. Reload karanna reloadpay.hutch.lk ekata gihin number eka, amount eka "
                  "dala card eken pay karanna. Hutch Self-Care app eken puluwan.",
        "used_articles": ["how-to-reload", "self-care-app"],
    })
    h = harness(model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI), language="en"))
    assert result.reply_text.startswith("Oyage balance eka LKR 420.00. Reload karanna reloadpay.hutch.lk")
    assert [c.title for c in result.citations] == ["How to reload a prepaid number", "Hutch Self-Care app"]
    assert [c.type for c in result.cards] == ["account"]
    assert model.payloads[0]["account_fact"] == "The customer's current main balance is LKR 420.00 (as of 2 Oct, 12:00)."
    assert result.pending_question is None
    record = next(r for r in h.telemetry.records if r.purpose == "FAQ_ANSWER")
    assert record.outcome == "OK" and record.conversation_id == conv


def test_guest_gets_the_steps_without_account_data() -> None:
    model = AnswerModel()
    h = harness(model)
    ctx = guest()
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert result.reply_text == "Use reloadpay.hutch.lk." and result.cards == []
    assert model.payloads[0]["account_fact"] is None and h.facade.calls["get_account"] == 0


def test_balance_is_only_added_for_reload_and_balance_topics() -> None:
    model = AnswerModel(lambda p: {"answer": "Call 1788.", "used_articles": ["contact-support"]})
    h = harness(model, {"customer care number": ["contact-support"]})
    h.model.on("customer care number eka mokakda", extraction(intent="FAQ", detected_language="si", faq_query="customer care number"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("customer care number eka mokakda")))
    assert result.reply_text == "Call 1788." and result.cards == []
    assert model.payloads[0]["account_fact"] is None and h.facade.calls["get_account"] == 0


def test_account_lookup_failure_still_answers_the_question() -> None:
    model = AnswerModel()
    h = harness(model)
    h.facade.fail_next["get_account"] = ResolveError("DEPENDENCY_UNAVAILABLE")
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert result.reply_text == "Use reloadpay.hutch.lk." and result.cards == []
    assert model.payloads[0]["account_fact"] is None


@pytest.mark.parametrize(
    "respond",
    [lambda p: {"answer": "Dial #123# to reload.", "used_articles": ["how-to-reload"]}, lambda p: ModelError("down")],
)
def test_rejected_or_failed_answer_falls_back_to_the_card_text(respond) -> None:
    h = harness(AnswerModel(respond))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert result.reply_text == RELOAD and "#123#" not in result.reply_text
    assert [c.title for c in result.citations] == ["How to reload a prepaid number"] and result.cards == []
    assert next(r for r in h.telemetry.records if r.purpose == "FAQ_ANSWER").outcome in {"NOT_GROUNDED", "MODEL_ERROR"}


def test_answer_is_skipped_when_the_turn_deadline_is_near() -> None:
    model = AnswerModel()
    h = harness(model)
    h.service._budgets[Channel.TEXT] = 1.0  # below MIN_REWRITE_SECONDS once the turn starts
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert result.reply_text == RELOAD and model.payloads == []


def test_answer_is_kept_for_audit_with_its_source_cards() -> None:
    h = harness(AnswerModel())
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert h.repo.source_texts[result.message_id] == RELOAD


def test_answer_is_not_machine_rewritten_again() -> None:
    from test_rewrite import RewriteModel

    from resolve.conversation.rewrite import ReplyRewriter

    h = harness(AnswerModel())
    rewriter = RewriteModel()
    h.service._rewriter = ReplyRewriter(rewriter)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text(RELOAD_SI)))
    assert result.reply_text == "Use reloadpay.hutch.lk." and rewriter.sources == []
