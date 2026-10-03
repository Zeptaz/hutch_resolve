"""Replies in the customer's language: auto-detected, rewritten by the model, fact-checked by code."""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import ACCOUNT_A, ACCOUNT_E, Harness, customer, text
from fakes import FakeModel, extraction
from resolve.conversation.model import ModelError, ModelReply
from resolve.conversation.rewrite import ReplyRewriter, preserves_facts

SINGLISH = extraction(complaint_type="BALANCE_RECHARGE", detected_language="si", script="LATIN")


class RewriteModel:
    """Returns a scripted rewrite; records the English source it was given."""

    provider, model_name = "fake", "fake-rewriter"

    def __init__(self, transform=lambda english: f"[si] {english}") -> None:
        self.transform = transform
        self.sources: list[str] = []
        self.styles: list[str] = []

    async def generate_json(self, *, system, prompt, schema):
        payload = json.loads(prompt)
        self.sources.append(payload["reply_en"])
        self.styles.append(payload["target_style"])
        result = self.transform(payload["reply_en"])
        if isinstance(result, Exception):
            raise result
        return ModelReply(text=json.dumps({"reply": result}), provider="fake", model="fake-rewriter", input_tokens=7, output_tokens=9)


def harness(transform=lambda english: f"[si] {english}") -> tuple[Harness, RewriteModel]:
    h = Harness(model=FakeModel())
    rewriter_model = RewriteModel(transform)
    h.service._rewriter = ReplyRewriter(rewriter_model)
    return h, rewriter_model


@pytest.mark.parametrize(
    ("source", "rewrite", "ok"),
    [
        ("Balance is LKR 420.00 on 2 Oct, 12:00.", "Oyage balance eka LKR 420.00, Oct 2, 12:00 ta.", True),
        ("Balance is LKR 420.00.", "Oyage balance eka LKR 42.00.", False),  # changed amount
        ("Balance is LKR 420.00.", "Balance LKR 420.00, refund LKR 500 karanawa.", False),  # added number
        ("Balance is LKR 80.00.", "Balance is LKR 8000.", False),  # decimal/magnitude substitution
        ("Debit was LKR -70.00.", "Debit was LKR 70.00.", False),  # sign reversal
        ("Paid LKR 80.00 then LKR 20.00.", "Paid LKR 20.00 then LKR 80.00.", True),  # lexical check cannot prove association
        ("Request ID 013eac3f-c7a3-47f9-9f15-c625026d75a0.", "Request ID eka 013eac3f-c7a3-47f9-9f15-c625026d75a0.", True),
        ("Request ID 013eac3f-c7a3-47f9-9f15-c625026d75a0.", "Request ID eka 013eac3f.", False),  # truncated ID
        ("Line SIM-LK-0001 is active.", "SIM-LK-0001 line eka active.", True),
        ("Nothing changed.", "Mukuth wenas une na. Balanna https://evil.example", False),  # new link
        ("0.8 GB was charged as LKR 80.", "0.8 GB ekata LKR 80k charge wela.", False),  # reads as 80,000
        ("It adds up to LKR 420.", "LKR 420kata hariyanawa.", True),  # Singlish ending, not a magnitude
        ("It adds up to LKR 420.", "LKR 420 m hariyanawa.", False),
        ("3 options: A, B, C.", "Options 3k thiyenawa: A, B, C.", True),  # Singlish count, not 3,000
        ("You paid LKR 80.", "Oya LKR 80k gewwa.", False),
        ("You paid 400.", "Oya 400k gewwa.", False),
        ("You paid 1.5.", "Oya 1.5k gewwa.", False),
    ],
)
def test_fact_guard(source: str, rewrite: str, ok: bool) -> None:
    assert preserves_facts(source, rewrite) is ok


def test_singlish_message_gets_singlish_style_reply_without_language_picker() -> None:
    h, model = harness()
    h.model.on("Mage reload eka watila na", SINGLISH)
    ctx = customer(ACCOUNT_E)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("Mage reload eka watila na"), language="en"))  # UI still says English
    # Text-chat findings and offers are rewritten into the customer's style (CB-001); facts survive.
    assert result.reply_text.startswith("[si] ") and "LKR 500" in result.reply_text
    assert model.styles == ["romanized Sinhala (Singlish), the way people in Sri Lanka text each other"]
    assert h.state(conv).language == "si" and h.state(conv).script == "LATIN"
    assert [r.outcome for r in h.telemetry.records if r.purpose == "REPLY_REWRITE"] == ["OK"]
    assert any(c.type == "confirmation" for c in result.cards)  # the English card stays authoritative


def test_language_sticks_for_button_clicks() -> None:
    h, model = harness()
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, text("mage balance eka adu wela"), language="en"))
    card = next(c for c in offered.cards if c.type == "confirmation").data
    decided = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(card.id),
                                         "proposal_hash": card.proposal_hash, "decision": "DECLINE"}, language="en"))
    assert "Nothing on your account was changed" in decided.reply_text
    # Only the findings/offer turn was rewritten; the decline is an outcome and stays deterministic.
    assert len(model.sources) == 1 and not decided.reply_text.startswith("[si]")


def test_financial_case_rewrite_that_changes_an_amount_is_rejected() -> None:
    h, model = harness(lambda english: english.replace("420", "520"))
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert "reconcile to LKR 420" in result.reply_text and "520" not in result.reply_text
    assert len(model.sources) == 1  # the rewrite was attempted...
    assert [r.outcome for r in h.telemetry.records if r.purpose == "REPLY_REWRITE"] == ["FACT_CHECK"]  # ...and refused
    assert h.repo.source_texts == {}


def test_case_rewrite_that_drops_the_target_label_is_rejected() -> None:
    h, _ = harness(lambda english: english.replace("Synthetic video alerts", "e service eka"))
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert "Synthetic video alerts" in result.reply_text and not result.reply_text.startswith("[si]")
    assert [r.outcome for r in h.telemetry.records if r.purpose == "REPLY_REWRITE"] == ["FACT_CHECK"]


def test_case_rewrite_that_drops_the_question_is_rejected() -> None:
    h, _ = harness(lambda english: f"[si] {english}".replace("?", "."))
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert result.reply_text.endswith("Shall I go ahead?") and not result.reply_text.startswith("[si]")


def test_case_rewrite_is_told_which_labels_to_keep() -> None:
    seen = []

    class Spy(RewriteModel):
        async def generate_json(self, *, system, prompt, schema):
            seen.append(json.loads(prompt)["keep_exact"])
            return await super().generate_json(system=system, prompt=prompt, schema=schema)

    h = Harness(model=FakeModel())
    h.service._rewriter = ReplyRewriter(Spy())
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert seen == [["Synthetic video alerts"]]


def test_voice_case_reply_is_never_rewritten() -> None:
    from resolve.conversation.dto import Channel

    h, model = harness()
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela"), channel=Channel.VOICE))
    assert "reconcile to LKR 420" in result.reply_text and model.sources == []
    assert "\n" not in result.reply_text  # spoken as one continuous reply


def test_text_case_reply_is_split_into_paragraphs() -> None:
    h = Harness(model=FakeModel())
    h.model.on("my balance dropped after recharge", extraction(complaint_type="BALANCE_RECHARGE"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("my balance dropped after recharge")))
    paragraphs = result.reply_text.split("\n\n")
    assert len(paragraphs) >= 2 and paragraphs[-1].startswith("I can ") and paragraphs[-1].endswith("?")


def test_rewriter_outage_keeps_english() -> None:
    h, _ = harness(lambda english: ModelError("unavailable"))
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert "reconcile to LKR 420" in result.reply_text


def test_english_messages_are_not_rewritten() -> None:
    h, model = harness()
    h.model.on("my balance dropped after recharge", extraction(complaint_type="BALANCE_RECHARGE"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("my balance dropped after recharge")))
    assert not result.reply_text.startswith("[si]") and model.sources == []


def test_short_ok_does_not_switch_language_back_to_english() -> None:
    h, _ = harness()
    h.model.on("mage balance eka adu wela", SINGLISH)
    h.model.on("ok", extraction(intent="OTHER", detected_language="en"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    h.send(ctx, h.turn(conv, text("ok")))
    assert h.state(conv).language == "si"


def test_tamil_script_targets_tamil_script() -> None:
    h, model = harness(lambda english: f"[ta] {english}")
    h.model.on("என் பேலன்ஸ் குறைந்துவிட்டது", extraction(complaint_type="BALANCE_RECHARGE", detected_language="ta", script="TAMIL"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("என் பேலன்ஸ் குறைந்துவிட்டது")))
    assert model.styles == ["Tamil in Tamil script"]


def test_english_original_is_kept_with_the_rewritten_reply() -> None:
    h, model = harness()
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert result.reply_text.startswith("[si] ")
    assert list(h.repo.source_texts.values()) == model.sources  # English original saved with the message
    assert result.reply_text == f"[si] {model.sources[0]}"


def test_reviewed_faq_text_is_never_machine_rewritten() -> None:
    h, model = harness()
    h.model.on("package eka activate karanne kohomada", extraction(intent="FAQ", detected_language="si", script="LATIN", faq_query="package activation"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("package eka activate karanne kohomada")))
    assert result.reply_text.startswith("HUTCH self-care publicly lists plan activation.")
    assert result.citations and model.sources == []


def test_critical_operation_outcome_never_enters_rewriter() -> None:
    from uuid import uuid4

    from resolve.conversation.ports import TurnDraft

    h, model = harness(lambda _english: "Your action succeeded.")
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    state = h.state(conv)
    turn = h.turn(conv, text("hello"))
    draft = TurnDraft(reply_text="Your action is pending.", operation_ids=[uuid4()])

    result = asyncio.run(h.service._localize(ctx, turn, draft, state))

    assert result is draft
    assert model.sources == []


def test_voice_turn_skips_rewrite_when_the_deadline_is_near() -> None:
    from resolve.conversation.dto import Channel

    h, model = harness()
    h.service._budgets[Channel.VOICE] = 1.0  # less than MIN_REWRITE_SECONDS left after extraction
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela"), channel=Channel.VOICE))
    assert not result.reply_text.startswith("[si]") and model.sources == []
    assert "reconcile to LKR 420" in result.reply_text


def test_extraction_is_cut_to_the_remaining_turn_budget() -> None:
    import time

    model = FakeModel()
    model.delay = 2.0
    h = Harness(model=model)  # extractor's own budget is 6 s
    h.service._budgets[__import__("resolve.conversation.dto", fromlist=["Channel"]).Channel.TEXT] = 0.2
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    started = time.monotonic()
    result = h.send(ctx, h.turn(conv, text("slow model")))
    assert time.monotonic() - started < 1.0  # stopped at the turn deadline, not the 6 s extractor budget
    assert result.pending_question.code == "CHOOSE_COMPLAINT_TYPE"  # structured fallback
    assert [r.outcome for r in h.telemetry.records] == ["TIMEOUT"]


def test_singlish_ak_glued_to_the_same_amount_is_respaced() -> None:
    from resolve.conversation.rewrite import soften_singlish_k

    source = "0.8 GB was charged as LKR 80."
    rewrite = soften_singlish_k("0.8 GB ekata LKR 80k charge wela.", source)
    assert rewrite == "0.8 GB ekata LKR 80 ak charge wela." and preserves_facts(source, rewrite)
    assert soften_singlish_k("LKR 800k charge wela.", source) == "LKR 800k charge wela."  # not the source amount
    assert soften_singlish_k("LKR 80 k charge wela.", source) == "LKR 80 ak charge wela."
