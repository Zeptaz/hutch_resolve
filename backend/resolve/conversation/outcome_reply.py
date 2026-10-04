"""Explain Resolve's investigation outcome in words: the chat in full, Voice in a few sentences.

Every amount, time, name and number here is quoted from the outcome Resolve computed; nothing is added up
or estimated in this module except counting items Resolve already listed. Both channels explain the same
classification, so a caller hears the same conclusion the chat shows.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from . import templates as t
from .dto import InvestigationOutcome, OutcomeAnomaly, OutcomeItem, OutcomeLine


def _lkr(amount_minor: int | None) -> str:
    return t.format_lkr(abs(amount_minor or 0))


def _time(value: datetime | None) -> str | None:
    return t.format_time(value).split(", ")[-1] if value else None


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _minutes(seconds: int | None) -> str | None:
    if not seconds:
        return None
    minutes = max(1, round(seconds / 60))
    return f"{minutes}-minute"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 or word == "SMS" else f"{count} {word}s"


def _calls(line: OutcomeLine, *, short: bool) -> str:
    items = line.items
    if len(items) == 1:
        call = items[0]
        length = _minutes(call.duration_seconds)
        if short:
            return f"{_lkr(line.amount_minor)} on a call"
        return f"{_lkr(line.amount_minor)} for a {length + ' ' if length else ''}call to {call.counterparty or 'another number'}"
    text = f"{_lkr(line.amount_minor)} for {_plural(len(items), 'call')}"
    if short:
        return f"{_lkr(line.amount_minor)} on {_plural(len(items), 'call')}"
    numbers = Counter(item.counterparty for item in items if item.counterparty)
    if numbers:
        top, count = numbers.most_common(1)[0]
        if count > 1:
            top_total = sum(-item.amount_minor for item in items if item.counterparty == top)
            text += f" ({count} of them to {top}, {_lkr(top_total)})"
    return text


def _product_name(item: OutcomeItem, category: str) -> str:
    return item.product_name or ("your package" if category == "PACKAGES" else "a value-added service")


def _settled(item: OutcomeItem) -> bool:
    """A charge and its refund cancel out; the refund sentence explains both."""
    return bool(item.reversed or item.reverses_reference)


def _live(line: OutcomeLine) -> OutcomeLine | None:
    items = [item for item in line.items if not _settled(item)]
    if not items:
        return None
    if len(items) == len(line.items):
        return line
    return line.model_copy(update={"items": items, "count": len(items),
                                   "amount_minor": sum(abs(item.amount_minor) for item in items)})


def _debit_phrases(outcome: InvestigationOutcome, *, short: bool) -> list[str]:
    phrases: list[str] = []
    for full in outcome.breakdown:
        line = _live(full) if full.direction == "DEBIT" else None
        if line is None:
            continue
        if line.category == "CALLS":
            phrases.append(_calls(line, short=short))
        elif line.category == "SMS":
            phrases.append(f"{_lkr(line.amount_minor)} {'on' if short else 'for'} {line.count} SMS")
        elif line.category in {"PACKAGES", "VAS"}:
            word = "on" if short else "for"
            phrases.extend(f"{_lkr(item.amount_minor)} {word} {_product_name(item, line.category)}" for item in line.items)
        elif line.category == "DATA":
            phrases.append(f"{_lkr(line.amount_minor)} {'on' if short else 'for'} out-of-bundle data")
        elif line.category == "TRANSFERS":
            phrases.append(f"{_lkr(line.amount_minor)} {'in' if short else 'as'} a balance transfer")
        elif line.category == "FEES":
            phrases.append(f"a {_lkr(line.amount_minor)} fee")
        elif line.category == "USAGE":
            phrases.append(f"{_lkr(line.amount_minor)} in usage charges")
        else:
            phrases.append(f"{_lkr(line.amount_minor)} in other charges")
    return phrases


def _credit_phrase(outcome: InvestigationOutcome) -> str:
    parts = []
    for full in outcome.breakdown:
        line = _live(full) if full.direction == "CREDIT" else None
        if line is None:
            continue
        if line.category == "RECHARGES":
            parts.append(f"reloaded {_lkr(line.amount_minor)}" + (f" in {line.count} top-ups" if line.count > 1 else ""))
        elif line.category == "PROMOTIONS":
            parts.append(f"received a {_lkr(line.amount_minor)} bonus")
        elif line.category == "REFUNDS":
            parts.append(f"got {_lkr(line.amount_minor)} back as a refund")
    return _join(parts)


def _refund_note(outcome: InvestigationOutcome) -> str | None:
    for line in outcome.breakdown:
        for item in line.items:
            if item.reverses_reference:
                charged = _find(outcome, item.reverses_reference)
                name = charged.product_name if charged and charged.product_name else "a charge"
                charged_at = _time(charged.occurred_at) if charged else None
                refunded_at = _time(item.occurred_at)
                return (f"You were charged {_lkr(item.amount_minor)} for {name}{' at ' + charged_at if charged_at else ''}, "
                        f"and it was refunded{' at ' + refunded_at if refunded_at else ''}, so it no longer affects your balance.")
    return None


def _find(outcome: InvestigationOutcome, reference: str) -> OutcomeItem | None:
    return next((item for line in outcome.breakdown for item in line.items if item.reference == reference), None)


def _anomaly_sentence(anomaly: OutcomeAnomaly, outcome: InvestigationOutcome) -> str | None:
    when = _time(anomaly.occurred_at)
    if anomaly.code == "DUPLICATE_CHARGE":
        first = _find(outcome, anomaly.reference or "")
        name = first.product_name if first and first.product_name else "the same item"
        return (f"You were charged {_lkr(anomaly.amount_minor)} for {name} twice{' at ' + when if when else ''}, "
                f"with the same reference. The second {_lkr(anomaly.amount_minor)} charge looks like a duplicate.")
    if anomaly.code == "RECHARGE_NOT_CREDITED":
        status = (anomaly.fulfilment_status or "").lower() or "not complete"
        return (f"Your {_lkr(anomaly.amount_minor)} top-up payment{' at ' + when if when else ''} was taken, but it "
                f"hasn't been added to your balance (fulfilment is {status}). Please don't pay again for it.")
    if anomaly.code == "VAS_CONSENT_UNVERIFIED":
        charged = f" {_lkr(anomaly.amount_minor)}" if anomaly.amount_minor else ""
        return (f"You were charged{charged} for {anomaly.product_name or 'a value-added service'}, but I can't find "
                "any record that you subscribed to it.")
    if anomaly.code == "REVERSAL_MISMATCH":
        return ("The records don't agree with each other: a refund on your account doesn't match the charge it "
                "reverses, so I can't give a final answer or change your account.")
    return None


def _history_sentence(outcome: InvestigationOutcome) -> str | None:
    resolved = [item for item in outcome.history if item.resolution]
    if not resolved:
        return None
    item = resolved[0]
    return f"You contacted us about this before ({item.case_ref}); it was resolved: {item.resolution}."


def compose(outcome: InvestigationOutcome, *, voice: bool) -> str:
    """The explanation for a balance or VAS investigation (no offer; the caller appends one if needed)."""
    debits = _debit_phrases(outcome, short=voice)
    if voice and len(debits) > 4:
        debits = debits[:3] + ["the rest on smaller charges"]
    credits = _credit_phrase(outcome)
    kind = outcome.classification.value
    notes = set(outcome.notes)
    sentences: list[str] = []

    if kind == "INSUFFICIENT_EVIDENCE":
        what = f"where {_lkr(outcome.claimed_minor)} went" if outcome.claimed_minor else "what happened to your balance"
        sentences.append("I couldn't get a complete balance history for your line in this period (the starting or "
                         f"closing balance isn't available), so I can't work out {what} without guessing.")
        return " ".join(sentences)

    if outcome.breakdown:
        start = f"You started with {_lkr(outcome.opening_minor)}" if outcome.opening_minor is not None else "In this period you"
        if credits:
            start += f" and {credits}" if outcome.opening_minor is not None else f" {credits}"
        sentences.append(start + ".")
        if debits:
            sentences.append(("Then " if not voice else "Then ") + _join(debits) + " was deducted." if len(debits) == 1
                             else ("After that: " if not voice else "Then ") + _join(debits) + ".")
    if "RECHARGE_ATTEMPT_FAILED_NO_CHARGE" in notes:
        sentences.append("One top-up attempt failed, and no money was taken for it.")
    refund = _refund_note(outcome)
    if refund:
        sentences.append(refund)

    if kind == "EXPLAINED":
        if outcome.observed_minor is not None:
            sentences.append(f"That leaves {_lkr(outcome.observed_minor)}, which matches your recorded balance. "
                             "Every deduction is accounted for, so nothing looks wrong.")
        if "CLAIM_EXCEEDS_RECORDS" in notes and outcome.claimed_minor:
            sentences.append(f"I can only find {_lkr(outcome.explained_minor)} in deductions, not the "
                             f"{_lkr(outcome.claimed_minor)} you mentioned, and your balance agrees with these records.")
        elif "REPORTED_BALANCE_DIFFERS" in notes and outcome.observed_minor is not None:
            sentences.append(f"Your recorded balance is {_lkr(outcome.observed_minor)}; if you see a different "
                             "amount, it may be from before or after this period.")
    else:
        for anomaly in outcome.anomalies:
            sentence = _anomaly_sentence(anomaly, outcome)
            if sentence:
                sentences.append(sentence)
        gap = next((item for item in outcome.anomalies if item.code == "BALANCE_GAP"), None)
        if gap is not None:
            if outcome.claimed_minor and outcome.explained_minor is not None:
                sentences.append(f"I found records explaining {_lkr(outcome.explained_minor)} of the "
                                 f"{_lkr(outcome.claimed_minor)} you reported, but I can't find any record for the "
                                 f"remaining {_lkr(outcome.unexplained_minor)}.")
                if not voice and outcome.expected_minor is not None and outcome.observed_minor is not None:
                    sentences.append(f"From these records your balance should be {_lkr(outcome.expected_minor)}, "
                                     f"but it is {_lkr(outcome.observed_minor)}.")
            elif gap.direction == "MISSING":
                sentences.append(f"Your balance should be {_lkr(outcome.expected_minor)} from these records, but it is "
                                 f"{_lkr(outcome.observed_minor)}: {_lkr(gap.amount_minor)} is missing with no record behind it.")
            else:
                sentences.append(f"Your balance is {_lkr(gap.amount_minor)} higher than these records explain.")
    history = _history_sentence(outcome)
    if history and not voice:
        sentences.append(history)
    if kind == "EXPLAINED":
        sentences.append("If you'd still like a person to check, just ask.")
    return " ".join(sentences)


def review_offer(outcome: InvestigationOutcome | None, *, voice: bool) -> str | None:
    """A plain question for a human review, naming the unexplained amount when there is one."""
    if outcome is None or outcome.escalation != "OFFER":
        return None
    amount = outcome.unexplained_minor
    target = f"the unexplained {_lkr(amount)}" if amount else "this"
    if voice:
        return (f"I can send {target} to our review team; nothing on your account changes now. To agree, please press "
                "'Yes, go ahead' on your screen, or 'No, leave it'.")
    return f"Would you like me to send {target} to our review team? Nothing on your account changes until they check it."
