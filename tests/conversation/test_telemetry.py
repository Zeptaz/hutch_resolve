"""T-04 model usage telemetry: every attempt recorded, nothing private logged."""

from __future__ import annotations

import dataclasses

from conftest import ACCOUNT_A, Harness, customer, text
from fakes import FakeModel, extraction
from resolve.conversation.extraction import PROMPT_VERSION
from resolve.conversation.model import ModelError

SECRET = "my NIC is 991234567V and I paid 500"


def test_successful_call_records_usage_without_text(hm: Harness) -> None:
    hm.model.on(SECRET, extraction(complaint_type="BALANCE_RECHARGE", amount_lkr=500))
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, text(SECRET)))

    [record] = hm.telemetry.records
    assert (record.purpose, record.outcome, record.attempt) == ("EXTRACTION", "OK", 1)
    assert (record.provider, record.model) == ("fake", "fake-model")
    assert (record.input_tokens, record.output_tokens) == (10, 5)
    assert record.prompt_version == PROMPT_VERSION
    assert record.request_id == ctx.request_id and record.conversation_id == conv
    flattened = " ".join(str(v) for v in dataclasses.asdict(record).values())
    assert "991234567V" not in flattened and "paid" not in flattened


def test_repair_records_both_attempts() -> None:
    h = Harness(model=FakeModel().on("x", "not json", extraction(intent="OTHER")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("x")))
    assert [(r.attempt, r.outcome) for r in h.telemetry.records] == [(1, "INVALID_OUTPUT"), (2, "OK")]


def test_failures_are_recorded_with_category_only() -> None:
    h = Harness(model=FakeModel().on("x", ModelError("ResourceExhausted")))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("x")))
    [record] = h.telemetry.records
    assert (record.outcome, record.error_type, record.input_tokens) == ("MODEL_ERROR", "ResourceExhausted", None)
    assert record.model == "fake-model"  # configured model when no reply came back


def test_timeout_is_recorded() -> None:
    model = FakeModel()
    model.delay = 5
    h = Harness(model=model, budget=0.05)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("slow")))
    assert [r.outcome for r in h.telemetry.records] == ["TIMEOUT"]


def test_telemetry_outage_never_breaks_the_turn(hm: Harness) -> None:
    hm.telemetry.fail = True
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    result = hm.send(ctx, hm.turn(conv, text("hello")))
    assert result.conversation_version == 2


def test_structured_turns_make_no_model_calls(hm: Harness) -> None:
    ctx = customer(ACCOUNT_A)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, {"type": "category_selection", "complaint_type": "DATA_DEPLETION"}))
    assert hm.telemetry.records == [] and hm.model.prompts == []
