"""Questions about the line's own history: reloads, "the one before that", and charges by kind.

"When did my last reload take place?" used to answer with the balance; "the reload before that?" and
"how much was it?" fell back to the complaint menu; "how much did you cut for VAS?" had no amounts.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import ACCOUNT_A, SIM_END, Harness, customer, text
from fakes import FakeModel, extraction
from resolve.conversation.dto import Channel

DAY = timedelta(days=1)


def _history(h: Harness) -> None:
    rows = h.facade.activity[ACCOUNT_A]
    rows += [
        (SIM_END - timedelta(hours=3), "RECHARGE", 100_000, None, "PORTAL", False),
        (SIM_END - 8 * DAY, "RECHARGE", 30_000, None, "RETAILER", False),
        (SIM_END - 20 * DAY, "RECHARGE", 50_000, None, "APP", False),
        (SIM_END - timedelta(hours=2), "VAS_CHARGE", -6_000, "Synthetic video alerts", None, False),
        (SIM_END - 12 * DAY, "VAS_CHARGE", -6_000, "Synthetic video alerts", None, False),
        (SIM_END - 5 * DAY, "VAS_CHARGE", -1_100, "Synthetic daily news alerts", None, True),
        (SIM_END - timedelta(hours=1), "CALL_CHARGE", -1_200, None, None, False),
    ]


def _ask(h: Harness, ctx, conv, message: str, **fields):
    h.model.on(message, extraction(intent="ACCOUNT_ENQUIRY", **fields))
    return h.send(ctx, h.turn(conv, text(message), channel=ctx.channel))


def _open(channel: Channel = Channel.TEXT):
    h = Harness(model=FakeModel())
    _history(h)
    ctx = customer(ACCOUNT_A, channel)
    return h, ctx, h.open(ctx)


def test_last_reload_then_the_one_before_then_how_much() -> None:
    h, ctx, conv = _open()
    first = _ask(h, ctx, conv, "when did my last reload take place?", account_topic="RECHARGES", history_position="LATEST")
    assert first.reply_text.startswith("Your last reload was LKR 1,000.00 on ")
    assert "through the online portal" in first.reply_text
    assert [card.type for card in first.cards] == ["timeline"]

    before = _ask(h, ctx, conv, "when was the reload before that?", account_topic="RECHARGES", history_position="PREVIOUS")
    assert before.reply_text.startswith("The reload before that was LKR 300.00 on ")

    same = _ask(h, ctx, conv, "how much was it?", account_topic="RECHARGES", history_position="SAME")
    assert same.reply_text.startswith("That reload was LKR 300.00")

    older = _ask(h, ctx, conv, "and before that?", account_topic="RECHARGES", history_position="PREVIOUS")
    assert "LKR 500.00" in older.reply_text and "through the HUTCH app" in older.reply_text

    none = _ask(h, ctx, conv, "and before that?", account_topic="RECHARGES", history_position="PREVIOUS")
    assert none.reply_text.startswith("I can't find an earlier reload in the last 90 days of records")
    assert h.facade.calls["create_case"] == 0


def test_vas_total_counts_only_charges_that_were_not_refunded() -> None:
    h, ctx, conv = _open()
    result = _ask(h, ctx, conv, "how much did you cut for VAS?", account_topic="CHARGES", charge_category="VAS",
                  history_position="ALL")
    assert result.reply_text.startswith(
        "In the last 30 days you were charged LKR 120.00 for value-added services (VAS): LKR 120.00 for "
        "Synthetic video alerts (2 charges, the latest on ")
    assert "LKR 11.00 for Synthetic daily news alerts" in result.reply_text and "refunded" in result.reply_text
    assert "LKR 12.00" not in result.reply_text  # the call is not a VAS charge


def test_the_charge_before_that_stays_on_the_kind_just_discussed() -> None:
    h, ctx, conv = _open()
    last = _ask(h, ctx, conv, "when was my last VAS charge?", account_topic="CHARGES", charge_category="VAS",
                history_position="LATEST")
    assert last.reply_text.startswith("Your last VAS charge was LKR 60.00 for Synthetic video alerts")
    # The model often leaves the kind out of a follow-up ("ALL"); the previous answer's kind is kept.
    before = _ask(h, ctx, conv, "and the one before that?", account_topic="CHARGES", charge_category="ALL",
                  history_position="PREVIOUS")
    assert before.reply_text.startswith("The VAS charge before that was LKR 11.00 for Synthetic daily news alerts")
    assert "refunded later" in before.reply_text


def test_a_period_with_no_charges_says_so() -> None:
    h, ctx, conv = _open()
    result = _ask(h, ctx, conv, "how much was charged for SMS yesterday?", account_topic="CHARGES",
                  charge_category="SMS", history_position="ALL", time={"kind": "YESTERDAY"})
    assert result.reply_text.endswith("there were no charges for SMS on your line.")
    assert [card.type for card in result.cards] == ["timeline"] and result.cards[0].data.items == []


def test_no_reload_mentions_a_paid_top_up_that_was_not_credited() -> None:
    h = Harness(model=FakeModel())
    h.facade.uncredited[ACCOUNT_A] = [{"id": "60000000-0000-0000-0000-000000000002", "channel": "PORTAL",
                                       "amount_minor": 50_000, "payment_status": "CAPTURED",
                                       "fulfilment_status": "PENDING", "created_at": SIM_END - timedelta(hours=2)}]
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = _ask(h, ctx, conv, "when was my last reload?", account_topic="RECHARGES", history_position="LATEST")
    assert result.reply_text.startswith("I can't find any reload on your line")
    assert "There is a LKR 500.00 top-up from" in result.reply_text and "not yet added to your balance" in result.reply_text


def test_history_unavailable_is_said_plainly() -> None:
    from resolve.conversation.errors import ResolveError

    h, ctx, conv = _open()
    h.facade.fail_next["get_account_activity"] = ResolveError("DEPENDENCY_UNAVAILABLE")
    result = _ask(h, ctx, conv, "when was my last reload?", account_topic="RECHARGES", history_position="LATEST")
    assert result.reply_text == "I can't read your reload and charge records right now. Please try again in a moment."


def test_services_list_their_recent_charges_and_cancel_goes_to_the_vas_check() -> None:
    h, ctx, conv = _open()
    services = _ask(h, ctx, conv, "what VAS am I subscribed to right now?", account_topic="SERVICES")
    assert "Synthetic video alerts (active, renews automatically)" in services.reply_text
    assert "you were charged LKR 120.00 for value-added services (VAS)" in services.reply_text
    assert services.pending_question.code == "OFFER_CHARGE_CHECK"
    # "How do I cancel them?" is often read as a how-to question; after the services list it is the VAS request.
    h.model.on("how do I cancel them?", extraction(intent="FAQ", faq_query="cancel VAS"))
    cancel = h.send(ctx, h.turn(conv, text("how do I cancel them?")))
    assert cancel.reply_text.startswith("You don't need to do this yourself: I can stop it from renewing for you here")
    assert h.facade.calls["create_case"] == 1 and h.facade.calls["investigate"] == 1


def test_an_unrelated_turn_clears_the_history_pointer() -> None:
    h, ctx, conv = _open()
    _ask(h, ctx, conv, "when did my last reload take place?", account_topic="RECHARGES", history_position="LATEST")
    assert h.state(conv).history_focus is not None and h.state(conv).last_account_topic == "RECHARGES"
    h.model.on("thanks", extraction(intent="OTHER"))
    h.send(ctx, h.turn(conv, text("thanks")))
    assert h.state(conv).history_focus is None and h.state(conv).last_account_topic is None


def test_voice_gets_the_same_answer() -> None:
    h, ctx, conv = _open(Channel.VOICE)
    result = _ask(h, ctx, conv, "when did I last reload?", account_topic="RECHARGES", history_position="LATEST")
    assert result.reply_text.startswith("Your last reload was LKR 1,000.00 on ")
