"""Answer questions about the line's own history: reloads and what was charged, from Resolve's records.

"When was my last reload?", "and the one before that?", "how much was it?", "how much did you cut for
VAS this month?". Every amount and time is quoted from the postings Resolve returned; the only sums are
totals of the listed postings. A charge that was later reversed is named as refunded and left out of the
total. Nothing is estimated: no record in the period means the reply says so.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from . import templates as t
from .dto import TimelineData, TimelineItem
from .extraction import COLOMBO, ChargeCategory, HistoryPosition
from .state import HistoryFocus

DEFAULT_CHARGE_WINDOW = timedelta(days=30)
MAX_LISTED = 5

# Ledger kind -> the charge category a customer asks about.
_CATEGORY = {
    "VAS_CHARGE": ChargeCategory.VAS,
    "PACKAGE_RENEWAL": ChargeCategory.PACKAGES, "PACKAGE_PURCHASE": ChargeCategory.PACKAGES,
    "PACKAGE_ACTIVATION": ChargeCategory.PACKAGES,
    "CALL_CHARGE": ChargeCategory.CALLS, "SMS_CHARGE": ChargeCategory.SMS,
    "OUT_OF_BUNDLE_USAGE": ChargeCategory.DATA, "DATA_CHARGE": ChargeCategory.DATA,
    "FEE": ChargeCategory.FEES, "BALANCE_TRANSFER": ChargeCategory.TRANSFERS,
}
_CATEGORY_WORDS = {
    ChargeCategory.VAS: "value-added services (VAS)", ChargeCategory.PACKAGES: "packages",
    ChargeCategory.CALLS: "calls", ChargeCategory.SMS: "SMS", ChargeCategory.DATA: "data outside your package",
    ChargeCategory.FEES: "fees", ChargeCategory.TRANSFERS: "balance transfers", ChargeCategory.ALL: "charges",
}
_KIND_WORDS = {
    "VAS_CHARGE": "a value-added service", "PACKAGE_RENEWAL": "a package renewal",
    "PACKAGE_PURCHASE": "a package", "PACKAGE_ACTIVATION": "a package", "CALL_CHARGE": "calls",
    "SMS_CHARGE": "SMS", "OUT_OF_BUNDLE_USAGE": "data outside your package", "DATA_CHARGE": "data",
    "RATED_USAGE": "calls, SMS and data usage", "FEE": "a fee", "BALANCE_TRANSFER": "a balance transfer",
}
_CHANNEL_WORDS = {"APP": "the HUTCH app", "PORTAL": "the online portal", "RETAILER": "a retailer",
                  "USSD": "USSD", "CARD": "a reload card"}


class ActivityEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    amount_minor: int
    kind: str
    occurred_at: AwareDatetime
    reference: str | None = None
    reversal_of: UUID | None = None
    recharge_channel: str | None = None
    product_name: str | None = None
    reversed: bool = False


class RechargeAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    channel: str
    amount_minor: int
    payment_status: str
    fulfilment_status: str
    created_at: AwareDatetime


class AccountActivity(BaseModel):
    """Resolve's record of the line's recent MAIN-wallet postings, newest first."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    as_of: AwareDatetime
    since: AwareDatetime
    complete: bool
    entries: list[ActivityEntry]
    uncredited_recharges: list[RechargeAttempt] = []


@dataclass(frozen=True)
class HistoryAnswer:
    text: str
    entries: list[ActivityEntry]
    focus: HistoryFocus | None


def _when(value: datetime) -> str:
    day, time = t.format_time(value).split(", ")
    return f"{day} at {time}"


def _period(start: datetime, end: datetime, default: bool) -> str:
    if default:
        return f"In the last {round((end - start) / timedelta(days=1))} days"
    local_start, local_end = start.astimezone(COLOMBO), end.astimezone(COLOMBO)
    if local_start.time() == local_start.time().min and end - start == timedelta(days=1):
        return f"On {local_start.day} {local_start:%b}"
    if local_start.time() == local_start.time().min:
        return f"From {local_start.day} {local_start:%b} to {_when(end)}"
    return f"Between {_when(start)} and {_when(end)}"


def _charge_name(entry: ActivityEntry) -> str:
    return entry.product_name or _KIND_WORDS.get(entry.kind, entry.kind.replace("_", " ").lower())


def _reloads(activity: AccountActivity) -> list[ActivityEntry]:
    return [e for e in activity.entries if e.kind == "RECHARGE" and e.amount_minor > 0]


def _charges(activity: AccountActivity, category: ChargeCategory) -> list[ActivityEntry]:
    debits = [e for e in activity.entries if e.amount_minor < 0]
    if category is ChargeCategory.ALL:
        return debits
    return [e for e in debits if _CATEGORY.get(e.kind) is category]


def _reload_sentence(entry: ActivityEntry, *, lead: str) -> str:
    via = _CHANNEL_WORDS.get(entry.recharge_channel or "", "")
    return f"{lead} {t.format_lkr(entry.amount_minor)} on {_when(entry.occurred_at)}" + (f", through {via}" if via else "") + "."


def _charge_sentence(entry: ActivityEntry, *, lead: str) -> str:
    text = f"{lead} {t.format_lkr(-entry.amount_minor)} for {_charge_name(entry)} on {_when(entry.occurred_at)}"
    return text + (", and it was refunded later." if entry.reversed else ".")


def _uncredited_note(activity: AccountActivity, after: datetime | None, *, also: bool = True) -> str | None:
    attempts = [a for a in activity.uncredited_recharges if after is None or a.created_at > after]
    if not attempts:
        return None
    latest = attempts[0]
    if latest.payment_status == "FAILED":
        return (f"There was {'also ' if also else ''}a {t.format_lkr(latest.amount_minor)} top-up attempt on {_when(latest.created_at)} "
                "that failed, so no money was taken and nothing was added.")
    return (f"There is {'also ' if also else ''}a {t.format_lkr(latest.amount_minor)} top-up from {_when(latest.created_at)} "
            "that was paid but not yet added to your balance. Tell me if you'd like me to look into it.")


def _grouped(entries: list[ActivityEntry]) -> list[str]:
    """One phrase per product (or charge type), newest first, with its count and total."""
    groups: OrderedDict[str, list[ActivityEntry]] = OrderedDict()
    for entry in entries:
        groups.setdefault(_charge_name(entry), []).append(entry)
    phrases = []
    for name, items in groups.items():
        total = sum(-e.amount_minor for e in items)
        if len(items) == 1:
            phrases.append(f"{t.format_lkr(total)} for {name} on {_when(items[0].occurred_at)}")
        else:
            phrases.append(f"{t.format_lkr(total)} for {name} ({len(items)} charges, the latest on {_when(items[0].occurred_at)})")
    return phrases


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _ordinal_lead(index: int, noun: str) -> str:
    if index == 0:
        return f"Your last {noun} was"
    if index == 1:
        return f"The {noun} before that was"
    return f"Before that, {index + 1} {noun}s back, was"


def answer_history(activity: AccountActivity, *, topic: str, category: ChargeCategory | None,
                   position: HistoryPosition | None, window: tuple[datetime, datetime] | None,
                   focus: HistoryFocus | None) -> HistoryAnswer:
    """The reply for a RECHARGES or CHARGES enquiry; `focus` is what the previous answer pointed at."""
    follows_on = position in {HistoryPosition.PREVIOUS, HistoryPosition.SAME} or category is None
    if follows_on and focus is not None and focus.topic == topic and focus.category and category in {None, ChargeCategory.ALL}:
        category = ChargeCategory(focus.category)  # "the one before that" stays on the kind just discussed
    category = category or ChargeCategory.ALL
    items = _reloads(activity) if topic == "RECHARGES" else _charges(activity, category)
    noun = "reload" if topic == "RECHARGES" else ("VAS charge" if category is ChargeCategory.VAS else
                                                 "package charge" if category is ChargeCategory.PACKAGES else "charge")
    same_list = focus is not None and focus.topic == topic and (topic == "RECHARGES" or focus.category == category.value)
    if position is None:
        # "When did I reload?" means the latest reload; "how much was cut for VAS?" means the total.
        position = HistoryPosition.LATEST if topic == "RECHARGES" else HistoryPosition.ALL

    if position is HistoryPosition.ALL or window is not None:
        return _summary(activity, topic, category, items, window)

    index = 0
    if position is HistoryPosition.PREVIOUS:
        index = focus.index + 1 if same_list and focus is not None else 1
    elif position is HistoryPosition.SAME and same_list and focus is not None:
        index = focus.index
    if index >= len(items):
        days = round((activity.as_of - activity.since) / timedelta(days=1))
        if not items:
            text = (f"I can't find any {noun} on your line in the last {days} days of records." if topic == "RECHARGES"
                    else f"I can't find any charges for {_CATEGORY_WORDS[category]} on your line in the last {days} days of records.")
        else:
            text = f"I can't find an earlier {noun} in the last {days} days of records; the oldest one I have is from {_when(items[-1].occurred_at)}."
        if topic == "RECHARGES" and not items:
            note = _uncredited_note(activity, None, also=False)
            text = f"{text} {note}" if note else text
        return HistoryAnswer(text, [], focus)

    entry = items[index]
    lead = f"That {noun} was" if position is HistoryPosition.SAME and index > 0 else _ordinal_lead(index, noun)
    parts = [_reload_sentence(entry, lead=lead) if topic == "RECHARGES" else _charge_sentence(entry, lead=lead)]
    if topic == "RECHARGES" and index == 0:
        note = _uncredited_note(activity, entry.occurred_at)
        if note:
            parts.append(note)
    focus = HistoryFocus(topic=topic, category=None if topic == "RECHARGES" else category.value, index=index)
    return HistoryAnswer(" ".join(parts), [entry], focus)


def _summary(activity: AccountActivity, topic: str, category: ChargeCategory, items: list[ActivityEntry],
             window: tuple[datetime, datetime] | None) -> HistoryAnswer:
    default = window is None
    start, end = window or (activity.as_of - DEFAULT_CHARGE_WINDOW, activity.as_of)
    shown = [e for e in items if start <= e.occurred_at <= end]
    period = _period(start, end, default)
    focus = HistoryFocus(topic=topic, category=None if topic == "RECHARGES" else category.value, index=0)
    if topic == "RECHARGES":
        if not shown:
            return HistoryAnswer(f"{period} there were no reloads on your line.", [], focus)
        total = sum(e.amount_minor for e in shown)
        listed = [f"{t.format_lkr(e.amount_minor)} on {_when(e.occurred_at)}" for e in shown[:MAX_LISTED]]
        more = f" (the {MAX_LISTED} latest: {_join(listed)})" if len(shown) > MAX_LISTED else f": {_join(listed)}"
        count = "once" if len(shown) == 1 else f"{len(shown)} times"
        parts = [f"{period} you reloaded {count}, {t.format_lkr(total)} in total{more}."]
        note = _uncredited_note(activity, start)
        if note:
            parts.append(note)
        return HistoryAnswer(" ".join(parts), shown, focus)

    what = _CATEGORY_WORDS[category]
    if not shown:
        return HistoryAnswer(f"{period} there were no charges for {what} on your line.", [], focus)
    kept = [e for e in shown if not e.reversed]
    refunded = [e for e in shown if e.reversed]
    total = sum(-e.amount_minor for e in kept)
    phrases = _grouped(kept)
    if len(phrases) > MAX_LISTED:
        phrases = phrases[:MAX_LISTED - 1] + ["the rest on smaller charges"]
    parts = [f"{period} you were charged {t.format_lkr(total)} for {what}" + (f": {_join(phrases)}." if phrases else ".")]
    if refunded:
        parts.append(f"{_join(_grouped(refunded))} {'was' if len(refunded) == 1 else 'were'} refunded, so "
                     f"{'it is' if len(refunded) == 1 else 'they are'} not counted.")
    return HistoryAnswer(" ".join(parts), shown, focus)


def _label(entry: ActivityEntry) -> str:
    if entry.kind == "RECHARGE":
        via = _CHANNEL_WORDS.get(entry.recharge_channel or "")
        return f"Reload via {via}" if via else "Reload"
    name = _charge_name(entry)
    return name[0].upper() + name[1:] + (" (refunded)" if entry.reversed else "")


def timeline(entries: list[ActivityEntry]) -> TimelineData:
    """The records behind the answer, for the chat card (newest first)."""
    return TimelineData(items=[
        TimelineItem(evidence_id=e.id, occurred_at=e.occurred_at, recorded_at=e.occurred_at,
                     label=_label(e), amount_minor=e.amount_minor, bytes=None)
        for e in entries[:20]
    ])
