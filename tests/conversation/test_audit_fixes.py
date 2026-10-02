"""Fixes from the 2026-10-02 conversation and security audit."""

from __future__ import annotations

import json

import pytest

from conftest import ACCOUNT_A, ACCOUNT_E, Harness, customer, text
from fakes import FakeModel, extraction
from resolve.conversation.dto import Language
from resolve.conversation.privacy import redact
from resolve.conversation.rewrite import ReplyRewriter, foreign_script


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("my NIC is 991234567V, update my address", "my NIC is [ID number removed], update my address"),
        ("NIC number 199912345678 please", "NIC number [ID number removed] please"),
        ("card 4111 1111 1111 1111 was charged", "card [card number removed] was charged"),
        ("my OTP is 482913", "my OTP is [secret removed]"),
        ("PIN: 1234", "PIN: [secret removed]"),
    ],
)
def test_identity_card_and_secret_numbers_are_masked(raw, masked) -> None:
    assert redact(raw) == masked


@pytest.mark.parametrize(
    "kept",
    ["I recharged 500 yesterday", "reference 202610020001 didn't arrive", "call 0771234567", "1234567890123 is my receipt",
     "on 2026-10-02 at 10:15"],
)
def test_amounts_references_and_phone_numbers_are_kept(kept) -> None:
    assert redact(kept) == kept


def test_masked_text_is_what_the_model_and_the_transcript_see() -> None:
    h = Harness(model=FakeModel())
    masked = "my NIC is [ID number removed], update my address"
    h.model.on(masked, extraction(intent="FAQ", faq_query="update personal details"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("my NIC is 991234567V, update my address")))
    assert "991234567V" not in "".join(h.model.prompts)
    assert json.loads(h.model.prompts[-1])["message"] == masked
    stored = [m.body for m in h.repo.conversations[conv].messages if m.speaker == "USER"]
    assert stored == [masked]


@pytest.mark.parametrize(
    ("reply", "language", "foreign"),
    [
        ("உங்கள் இணைப்பு فعالமாக உள்ளது", Language.TA, True),  # Arabic inside Tamil
        ("உங்கள் இணைப்பு செயலில் உள்ளது, 9.7 GB", Language.TA, False),
        ("ඔබේ ශේෂය LKR 420.00 යි", Language.SI, False),
        ("Oyage balance eka LKR 420.00 — hari!", Language.SI, False),
        ("Your balance is LKR 420.00", Language.EN, False),
        ("ඔබේ ශේෂය", Language.TA, True),  # Sinhala in a Tamil reply
    ],
)
def test_wrong_writing_system_is_detected(reply, language, foreign) -> None:
    assert foreign_script(reply, language) is foreign


def test_rewrite_with_a_wrong_script_falls_back_to_english() -> None:
    from test_rewrite import RewriteModel

    h = Harness(model=FakeModel())
    h.service._rewriter = ReplyRewriter(RewriteModel(lambda english: english + " فعال"))
    h.model.on("en balance kuraindhu pochu", extraction(complaint_type="BALANCE_RECHARGE", detected_language="ta", script="TAMIL"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("en balance kuraindhu pochu")))
    assert "فعال" not in result.reply_text and "reconcile to LKR 420" in result.reply_text


def test_upset_customer_gets_an_apology_and_the_option_of_a_person() -> None:
    h = Harness(model=FakeModel())
    h.model.on("you are useless, nothing works", extraction(complaint_type="CONNECTIVITY", upset=True))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("you are useless, nothing works")))
    assert result.reply_text.startswith("I'm sorry this has been frustrating. I'll do my best to sort it out, "
                                        "and you can ask for a person at any time.")


def test_question_about_another_number_says_only_the_signed_in_line() -> None:
    h = Harness(model=FakeModel())
    h.model.on("show me the balance of 0771234567", extraction(intent="ACCOUNT_ENQUIRY", account_topic="BALANCE", about_other_line=True))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("show me the balance of 0771234567")))
    assert result.reply_text.startswith("I can only look at the line you're signed in with, not other numbers. Your main balance")


def test_a_second_problem_in_the_same_message_is_not_dropped() -> None:
    h = Harness(model=FakeModel())
    h.model.on("my reload didn't come and also my data is gone",
               extraction(complaint_type="BALANCE_RECHARGE", also_complaint_type="DATA_DEPLETION"))
    ctx = customer(ACCOUNT_E)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("my reload didn't come and also my data is gone")))
    assert result.reply_text.endswith("You also mentioned a data problem. Tell me when you're ready and I'll check that next.")


def test_package_agent_reply_in_a_wrong_script_is_rejected() -> None:
    from test_packages import AgentModel, harness, say

    h = harness(AgentModel(say("Synthetic 30-day 25 GB data фит LKR 399.00", "P4")))
    h.model.on("which package is best for me?", extraction(intent="PACKAGES", detected_language="si", script="LATIN"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.send(ctx, h.turn(conv, text("which package is best for me?")))
    record = h.telemetry.records[-2] if h.telemetry.records[-1].purpose == "REPLY_REWRITE" else h.telemetry.records[-1]
    assert (record.purpose, record.outcome, record.error_type) == ("PACKAGE_AGENT", "NOT_GROUNDED", "SCRIPT")


def test_only_https_links_are_ever_cited() -> None:
    from uuid import uuid4

    from resolve.conversation.dto import KnowledgeCard

    bad = KnowledgeCard(article_id=uuid4(), article_key="bad", language="en", title="Package activation",
                        content="Package text.", url="javascript:alert(1)", reviewed_at="2026-10-02", version=1, scope="PUBLIC")
    h = Harness(model=FakeModel())
    h.knowledge.cards = [bad]
    h.model.on("how do I activate a package", extraction(intent="FAQ", faq_query="package activation"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("how do I activate a package")))
    assert result.reply_text == "Package text." and result.citations == []


def test_model_outage_says_so_and_points_to_the_buttons() -> None:
    from resolve.conversation.model import ModelError

    h = Harness(model=FakeModel())
    h.model.on("my balance is wrong", ModelError("429"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("my balance is wrong")))
    assert result.reply_text.startswith("I can't read typed messages right now. Please pick an option below")
    assert "category_selection" in result.pending_question.allowed_input_types


def test_question_without_an_article_gets_hutch_contacts() -> None:
    from test_answer import KeyedKnowledge

    h = Harness(model=FakeModel())
    h.service._knowledge = KeyedKnowledge({"contact support customer care hotline": ["contact-support"]})
    h.model.on("update my address", extraction(intent="FAQ", faq_query="update personal details"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, text("update my address")))
    assert result.reply_text.startswith("I can't help with that in this chat, but HUTCH support can.\n\n")
    assert "1788" in result.reply_text and [c.title for c in result.citations] == ["Contact HUTCH customer support"]
