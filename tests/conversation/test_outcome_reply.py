"""Claims read from the customer's words, and the outcome explained in chat and Voice."""

from __future__ import annotations

import pytest

from resolve.conversation.claims import parse_claims
from resolve.conversation.dto import InvestigationOutcome
from resolve.conversation.outcome_reply import compose, review_offer


@pytest.mark.parametrize(("text", "expected"), [
    ("I recharged LKR 1000 this morning but my balance is only LKR 420", (None, 42_000)),
    ("After reloading 500, LKR 100 was deducted from my account", (10_000, None)),
    ("Rs. 100 got deducted after I reloaded", (10_000, None)),
    ("they deducted Rs 150 today", (15_000, None)),
    ("I was charged 60 for a subscription I never signed up for", (6_000, None)),
    ("LKR 1,250.50 is missing from my balance", (125_050, None)),
    ("only LKR 20 left after my reload", (None, 2_000)),
    ("my balance went down to 350 rupees", (None, 35_000)),
    ("I recharged LKR 1000", (None, None)),  # a recharge amount is never read as a loss
    ("I lost 2 GB of data", (None, None)),
    ("", (None, None)),
])
def test_claims_come_only_from_explicit_loss_or_balance_phrases(text, expected) -> None:
    assert parse_claims(text) == expected


def outcome(**overrides) -> InvestigationOutcome:
    base = {
        "classification": "PARTIALLY_EXPLAINED", "escalation": "OFFER", "currency": "LKR",
        "opening_minor": 20_000, "credits_minor": 50_000, "debits_minor": 7_300, "expected_minor": 62_700,
        "observed_minor": 60_000, "claimed_minor": 10_000, "explained_minor": 7_300, "unexplained_minor": 2_700,
        "breakdown": [
            {"category": "RECHARGES", "direction": "CREDIT", "amount_minor": 50_000, "count": 1, "evidence_ids": [],
             "items": [{"evidence_id": None, "kind": "RECHARGE", "amount_minor": 50_000, "posting_amount_minor": 50_000,
                        "occurred_at": "2026-10-02T03:00:00Z", "reference": "R"}]},
            {"category": "VAS", "direction": "DEBIT", "amount_minor": 1_100, "count": 1, "evidence_ids": [],
             "items": [{"evidence_id": None, "kind": "VAS_CHARGE", "amount_minor": -1_100, "posting_amount_minor": -1_100,
                        "occurred_at": "2026-10-02T03:15:00Z", "reference": "V", "product_name": "Daily news alerts"}]},
            {"category": "CALLS", "direction": "DEBIT", "amount_minor": 1_200, "count": 1, "evidence_ids": [],
             "items": [{"evidence_id": None, "kind": "CALL_CHARGE", "amount_minor": -1_200, "posting_amount_minor": -1_200,
                        "occurred_at": "2026-10-02T03:40:00Z", "reference": "C", "event_kind": "VOICE_CALL",
                        "counterparty": "071XXXX678", "duration_seconds": 180}]},
            {"category": "SMS", "direction": "DEBIT", "amount_minor": 5_000, "count": 25, "evidence_ids": [],
             "items": [{"evidence_id": None, "kind": "SMS_CHARGE", "amount_minor": -200, "posting_amount_minor": -5_000,
                        "occurred_at": "2026-10-02T05:35:00Z", "reference": "S", "event_kind": "SMS"}] * 25},
        ],
        "anomalies": [{"code": "BALANCE_GAP", "amount_minor": 2_700, "evidence_ids": [], "direction": "MISSING"}],
        "notes": [], "history": [],
    }
    base.update(overrides)
    return InvestigationOutcome.model_validate(base)


def test_partial_explanation_names_each_deduction_and_the_unexplained_amount() -> None:
    chat = compose(outcome(), voice=False)
    assert "You started with LKR 200.00 and reloaded LKR 500.00." in chat
    assert "LKR 11.00 for Daily news alerts" in chat and "LKR 12.00 for a 3-minute call to 071XXXX678" in chat
    assert ("I found records explaining LKR 73.00 of the LKR 100.00 you reported, but I can't find any record for "
            "the remaining LKR 27.00.") in chat
    assert review_offer(outcome(), voice=False) == (
        "Would you like me to send the unexplained LKR 27.00 to our review team? Nothing on your account changes "
        "until they check it.")


def test_voice_states_the_same_conclusion_more_briefly() -> None:
    chat, voice = compose(outcome(), voice=False), compose(outcome(), voice=True)
    assert "remaining LKR 27.00" in voice
    assert "071XXXX678" not in voice and len(voice) < len(chat)
    assert "'Yes, go ahead'" in review_offer(outcome(), voice=True)


def test_explained_outcome_proves_the_balance_and_does_not_offer_review() -> None:
    explained = outcome(classification="EXPLAINED", escalation="NOT_NEEDED", observed_minor=62_700,
                        unexplained_minor=0, anomalies=[])
    chat = compose(explained, voice=False)
    assert "That leaves LKR 627.00, which matches your recorded balance." in chat
    assert "If you'd still like a person to check, just ask." in chat
    assert review_offer(explained, voice=False) is None


def test_insufficient_evidence_never_guesses() -> None:
    missing = outcome(classification="INSUFFICIENT_EVIDENCE", breakdown=[], anomalies=[], opening_minor=None,
                      expected_minor=None, observed_minor=None, explained_minor=None, claimed_minor=15_000,
                      unexplained_minor=15_000)
    chat = compose(missing, voice=False)
    assert "can't work out where LKR 150.00 went without guessing" in chat
    assert "LKR 200.00" not in chat
