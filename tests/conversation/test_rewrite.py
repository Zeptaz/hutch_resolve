"""Replies in the customer's language: auto-detected, rewritten by the model, fact-checked by code."""

from __future__ import annotations

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
        ("Request ID 013eac3f-c7a3-47f9-9f15-c625026d75a0.", "Request ID eka 013eac3f-c7a3-47f9-9f15-c625026d75a0.", True),
        ("Request ID 013eac3f-c7a3-47f9-9f15-c625026d75a0.", "Request ID eka 013eac3f.", False),  # truncated ID
        ("Line SIM-LK-0001 is active.", "SIM-LK-0001 line eka active.", True),
        ("Nothing changed.", "Mukuth wenas une na. Balanna https://evil.example", False),  # new link
        ("0.8 GB was charged as LKR 80.", "0.8 GB ekata LKR 80k charge wela.", False),  # reads as 80,000
        ("It adds up to LKR 420.", "LKR 420kata hariyanawa.", True),  # Singlish ending, not a magnitude
        ("It adds up to LKR 420.", "LKR 420 m hariyanawa.", False),
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
    assert result.reply_text.startswith("[si] ")
    assert "Singlish" in model.styles[0]
    assert h.state(conv).language == "si" and h.state(conv).script == "LATIN"
    assert any(r.purpose == "REPLY_REWRITE" and r.outcome == "OK" for r in h.telemetry.records)


def test_language_sticks_for_button_clicks() -> None:
    h, model = harness()
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    offered = h.send(ctx, h.turn(conv, text("mage balance eka adu wela"), language="en"))
    card = next(c for c in offered.cards if c.type == "confirmation").data
    decided = h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(card.id),
                                         "proposal_hash": card.proposal_hash, "decision": "DECLINE"}, language="en"))
    assert decided.reply_text.startswith("[si] ")
    assert len(model.sources) == 2


def test_rewrite_that_changes_a_number_falls_back_to_english() -> None:
    h, _ = harness(lambda english: english.replace("420", "520"))
    h.model.on("mage balance eka adu wela", SINGLISH)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("mage balance eka adu wela")))
    assert "reconcile to LKR 420" in result.reply_text and "520" not in result.reply_text
    assert any(r.purpose == "REPLY_REWRITE" and r.outcome == "FACT_CHECK" for r in h.telemetry.records)


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
