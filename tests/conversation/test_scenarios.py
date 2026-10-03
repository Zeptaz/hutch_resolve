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
        (ACCOUNT_F, "VAS_DISPUTE", ["DEACTIVATE_VAS"], "CONFIRM_ACTION"),  # review kept as the next option
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
    hm.model.on("I want the old charge checked", extraction(intent="ACTION_DECISION", action_choice="CREATE_REVIEW_TICKET"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    first = hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    assert "does not decide the past charge" in first.reply_text
    assert "proof the service was activated" in first.reply_text
    assert offered(first) == ["DEACTIVATE_VAS"] and "I can also send this to our review team" in first.reply_text
    assert [c.action_type for c in hm.state(conv).pending_choices] == ["CREATE_REVIEW_TICKET"]

    switched = hm.send(ctx, hm.turn(conv, text("I want the old charge checked")))
    assert offered(switched) == ["CREATE_REVIEW_TICKET"]
    assert hm.facade.calls["confirm_action"] == 0  # choosing is not consent
    assert [c.action_type for c in hm.state(conv).pending_choices] == ["DEACTIVATE_VAS"]  # earlier offer kept


def test_f_model_cannot_choose_an_action_resolve_did_not_offer(hm: Harness) -> None:
    hm.model.on("send me settings", extraction(intent="ACTION_DECISION", action_choice="SEND_SETTINGS_INSTRUCTIONS"))
    ctx = customer(ACCOUNT_F)
    conv = hm.open(ctx)
    hm.send(ctx, hm.turn(conv, details("VAS_DISPUTE")))
    first = hm.state(conv).pending_proposal.action_type
    result = hm.send(ctx, hm.turn(conv, text("send me settings")))
    # Never the model's pick: at most the offer already on the table is shown again with its button.
    assert "SEND_SETTINGS_INSTRUCTIONS" not in [a.value for a in offered(result)]
    assert offered(result) == [first]
    proposed = [p.action_type for p in hm.facade.proposals.values()]
    assert "SEND_SETTINGS_INSTRUCTIONS" not in [a.value for a in proposed]


def decide(h: Harness, ctx, conv, result, value: str):
    card = next(c for c in result.cards if c.type == "confirmation").data
    return h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(card.id),
                                      "proposal_hash": card.proposal_hash, "decision": value}))


def test_f_every_option_reachable_with_buttons_only_after_decline(h: Harness) -> None:
    """No model at all: declining the first offer brings the next listed option."""
    ctx = customer(ACCOUNT_F)
    conv = h.open(ctx)
    first = h.send(ctx, h.turn(conv, details("VAS_DISPUTE")))
    second = decide(h, ctx, conv, first, "DECLINE")
    assert second.reply_text.startswith("Okay, I won't make that change.")
    assert offered(second) == ["CREATE_REVIEW_TICKET"] and second.pending_question.code == "CONFIRM_ACTION"
    done = decide(h, ctx, conv, second, "ACCEPT")
    assert len(done.operation_ids) == 1 and offered(done) == []
    assert h.state(conv).pending_choices == [] and h.state(conv).pending_proposal is None


def test_f_after_accepting_the_other_option_is_still_offered(h: Harness) -> None:
    """Stopping renewal and reviewing the past charge can both be wanted."""
    ctx = customer(ACCOUNT_F)
    conv = h.open(ctx)
    first = h.send(ctx, h.turn(conv, details("VAS_DISPUTE")))
    after = decide(h, ctx, conv, first, "ACCEPT")
    assert len(after.operation_ids) == 1 and "not been completed yet" in after.reply_text
    assert offered(after) == ["CREATE_REVIEW_TICKET"]


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
