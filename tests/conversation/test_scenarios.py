"""T-04: all six fixture scenarios through the conversation module.

B/C/E/F use stand-in investigation results (fakes.SCENARIO_BUILDERS) until
Harry's investigators exist; these tests check routing and safe wording, not
Harry's finding codes.
"""

from __future__ import annotations

import pytest

from conftest import ACCOUNT_A, ACCOUNT_B, ACCOUNT_C, ACCOUNT_D, ACCOUNT_E, ACCOUNT_F, Harness, customer, details, text
from fakes import extraction


def offered(result):
    return [c.data.action_type for c in result.cards if c.type == "confirmation"]


@pytest.mark.parametrize(
    ("account", "complaint", "expected_offer", "question"),
    [
        (ACCOUNT_A, "BALANCE_RECHARGE", ["DEACTIVATE_VAS"], "CONFIRM_ACTION"),
        (ACCOUNT_B, "DATA_DEPLETION", [], None),
        (ACCOUNT_C, "CONNECTIVITY", ["SEND_SETTINGS_INSTRUCTIONS"], "CONFIRM_ACTION"),
        (ACCOUNT_D, "BALANCE_RECHARGE", ["CREATE_REVIEW_TICKET"], "CONFIRM_ACTION"),
        (ACCOUNT_E, "BALANCE_RECHARGE", ["CREATE_REVIEW_TICKET"], "CONFIRM_ACTION"),
        (ACCOUNT_F, "VAS_DISPUTE", [], "CHOOSE_ACTION"),
    ],
)
def test_each_scenario_offers_only_what_resolve_made_eligible(h: Harness, account, complaint, expected_offer, question) -> None:
    ctx = customer(account)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details(complaint)))
    assert offered(result) == expected_offer
    assert (result.pending_question.code if result.pending_question else None) == question
    for finding in h.facade.cases[result.case_id].investigation.findings:
        assert finding.text in result.reply_text  # Resolve's wording, unchanged


def test_b_quota_and_out_of_bundle_charge_are_reported_separately(h: Harness) -> None:
    ctx = customer(ACCOUNT_B)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details("DATA_DEPLETION")))
    assert result.reply_text.count("LKR 80") == 1
    calc = next(c.data for c in result.cards if c.type == "calculation")
    assert calc.unit == "BYTES" and calc.observed == 0
    assert "recharge" not in result.reply_text.lower()


def test_c_reports_incident_without_inventing_a_fix_or_eta(h: Harness) -> None:
    ctx = customer(ACCOUNT_C)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details("CONNECTIVITY")))
    lowered = result.reply_text.lower()
    assert "no restoration time has been provided" in lowered
    for invented in ("will be fixed", "restored by", "within", "resolved soon", "repaired"):
        assert invented not in lowered
    assert "does not repair the network" in lowered


def test_e_payment_is_not_credit_and_never_asks_to_pay_again(h: Harness) -> None:
    ctx = customer(ACCOUNT_E)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, details("BALANCE_RECHARGE", amount_minor=50000)))
    lowered = result.reply_text.lower()
    assert "has not been added to your balance" in lowered
    assert "don't pay again" in lowered
    for bad in ("recharge again", "try recharging", "pay again to", "make another payment"):
        assert bad not in lowered


def test_f_separates_future_renewal_from_past_dispute_and_lets_customer_choose(hm: Harness) -> None:
    hm.model.on("stop the renewal please", extraction(intent="ACTION_DECISION", action_choice="DEACTIVATE_VAS"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    first = hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    assert "does not decide the past charge" in first.reply_text
    assert "proof the service was activated" in first.reply_text
    assert first.pending_question.allowed_input_types == ["text"]
    assert len(hm.state(conv).pending_choices) == 2
    assert hm.facade.calls["propose_action"] == 0

    chosen = hm.send(ctx, hm.turn(conv, text("stop the renewal please")))
    assert offered(chosen) == ["DEACTIVATE_VAS"]
    assert chosen.pending_question.code == "CONFIRM_ACTION"
    assert hm.facade.calls["confirm_action"] == 0  # choosing is not consent
    assert hm.state(conv).pending_choices == []


def test_f_model_cannot_choose_an_action_resolve_did_not_offer(hm: Harness) -> None:
    hm.model.on("send me settings", extraction(intent="ACTION_DECISION", action_choice="SEND_SETTINGS_INSTRUCTIONS"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    result = hm.send(ctx, hm.turn(conv, text("send me settings")))
    assert offered(result) == []
    assert hm.facade.calls["propose_action"] == 0


def test_f_review_choice_proposes_review_ticket(hm: Harness) -> None:
    hm.model.on("I want the old charge checked", extraction(intent="ACTION_DECISION", action_choice="CREATE_REVIEW_TICKET"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    result = hm.send(ctx, hm.turn(conv, text("I want the old charge checked")))
    assert offered(result) == ["CREATE_REVIEW_TICKET"]


def test_choices_from_another_case_do_not_apply(hm: Harness) -> None:
    hm.model.on("stop it", extraction(intent="ACTION_DECISION", action_choice="DEACTIVATE_VAS"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    first = hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    hm.send(ctx, hm.turn(conv, details("CONNECTIVITY")))  # new case replaces the open choice
    hm.send(ctx, hm.turn(conv, {"type": "case_selection", "case_id": str(first.case_id)}))
    assert hm.state(conv).pending_choices == []
    result = hm.send(ctx, hm.turn(conv, text("stop it")))
    assert offered(result) == []
