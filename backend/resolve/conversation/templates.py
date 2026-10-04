"""Deterministic reply templates.

English is authoritative and lives here. Other languages live in
`locales/<locale>.json` and are used **only** when the file's status is
`REVIEWED` by a fluent reviewer (T-04); otherwise replies fall back to English.
The plan forbids claiming native-language support that was not demonstrated.

A locale is the language plus how the customer writes it: `si` and `ta` are the
native scripts, `si-Latn` is romanized Sinhala ("Singlish") as typed in chat.
Every function taking `language` accepts a `Language` or a locale code.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .dto import ActionType, ComplaintType, DeliveryState, Handoff, Language, OperationStatus

_EN: dict[str, str] = {
    "choose_complaint": "What's the problem? Tell me in your own words, like \"my reload didn't arrive\" or \"my data finished too fast\", or pick an option below.",
    "describe_complaint": "Okay, your {complaint}. Tell me a bit more: when did it happen, and was there an amount? If you're not sure, just say \"today\".",
    "anything_else": "Is there anything else I can help you with? Tell me, or pick an option below.",
    "login_required": "To look at your account, please sign in first. I can still answer general questions without signing in.",
    "complaint_details": "When did this happen? Please give the start and end of the time window (up to 30 days) and any amount you noticed.",
    "invalid_window_order": "The end of the time window must be after the start. Please check the dates.",
    "invalid_window_length": "I can look at up to 30 days at a time. Please choose a shorter window.",
    "evidence_partial": "Some records I need are not available yet ({missing}), so I can't confirm the full picture.",
    "evidence_conflicting": "The records don't agree with each other, so I can't give a final answer or change your account. A person needs to review this.",
    "no_findings": "I checked the available records but found nothing to report for that time window.",
    "offer_action": "I can {action} for {target}. {consequences} Shall I go ahead?",
    "offer_action_voice": "I can {action} for {target}. {consequences} For security, please review this offer on your screen and press 'Yes, go ahead' or 'No, leave it'. Nothing will change until you press one of those buttons.",
    "other_options": "If you'd rather, I can also {options}. Just tell me, or answer this offer first and I'll suggest that next.",
    "other_options_voice": "Other eligible options are {options}. First answer this offer using the buttons on your screen; you can ask about those options afterward.",
    "confirm_prompt": "Please answer using the buttons on the offer above.",
    "confirm_prompt_voice": "For security, I can't take a spoken yes or no for this action. Please review the offer on your screen and tap 'Yes, go ahead' or 'No, leave it'. Nothing has changed yet.",
    "declined": "Okay, I won't make that change. Nothing on your account was changed.",
    "accepted": "Your request is recorded. {status}",
    "proposal_mismatch": "That request is no longer the one I'm waiting on. Please use the latest option shown.",
    "proposal_expired": "That offer has expired. Nothing was changed. I can check again if you'd like.",
    "proposal_invalidated": "Things changed since that offer was made, so it can no longer be used. Nothing was changed.",
    "case_selected": "Switched to your {complaint} case. Current status: {status}.",
    "case_not_found": "I couldn't find that case for this conversation.",
    "dependency_unavailable": "I can't reach that information right now. Please try again in a moment.",
    "not_allowed": "That isn't something I can do here.",
    "clarify_time_window": "When did this happen? For example: today, yesterday, or a date like 1 October.",
    "clarify_amount": "What amount did you notice, in rupees?",
    "clarify_target": "Which service or package do you mean?",
    "clarify_negation": "Just to be sure: did you subscribe to it or recharge yourself, or not?",
    # Restates only what the customer reported (type, amount); never model-written text.
    "ack_amount": "You mentioned {amount}.",
    "checked_window": "I looked at your {complaint} records for {window}.",
    "checked_default_window": "You didn't say when, so I looked at your {complaint} records for {window}.",
    "rechecked": "I re-checked with the corrected details ({window}).",
    "offer_still_open": "My earlier offer is still open.",
    "offer_still_open_voice": "The earlier offer is still pending. Please use the on-screen 'Yes, go ahead' or 'No, leave it' buttons.",
    "offer_locked_voice": "Before we move on, please answer the offer on your screen: tap 'Yes, go ahead' or 'No, leave it'. Nothing has changed yet.",
    "anything_else_voice": "Is there anything else I can help you with?",
    "call_goodbye_voice": "Thank you for calling HUTCH. Goodbye!",
    "no_pending_action": "There's nothing waiting for your confirmation right now.",
    "no_active_case": "There's no open request in this conversation yet. What would you like help with?",
    "case_status": "Your {complaint} request is {status}.",
    "receipt_ready": "A receipt is available for this request.",
    "human_needs_case": "I can pass this to a person with all the details. First, tell me what the problem is.",
    "default_escalation_reason": "Customer asked for a person to review this case.",
    "faq_none": "I don't have reviewed information on that yet. I can help with your balance, recharges, data, connection or service charges.",
    "faq_none_case": "I'm not sure I caught that, so here is where your request stands.",
    "guest_help": "I can answer general questions. To check your own account, please sign in first.",
    "account_balance": "Your {wallet} balance is {amount} (as of {as_of}).",
    "account_no_balance": "I couldn't find a balance for your account right now.",
    "account_incomplete": "Some account information may not be up to date.",
    # Each value is a posted ledger line quoted from Resolve's calculation, e.g. "-LKR 60.00".
    "vas_charge_lines": "Value-added service charges posted in these records: {amounts}.",
    # Each line is quoted from Resolve's ledger calculation; nothing is summed here.
    "ledger_lines": "Posted in these records: opening balance {opening}; {lines}; recorded balance {closing}.",
    "account_services": "Value-added services on your line: {services}.",
    "account_no_services": "You have no value-added services on your line right now.",
    "service_renews": "{name} ({status}, renews automatically)",
    "service_no_renewal": "{name} ({status}, does not renew)",
    "offer_charge_check": "I can't see each service's price here. Shall I check your charge records to see exactly what you were charged?",
    "offer_charge_check_stop": "I can stop a service from renewing for you here. Shall I check these charges against your records and set that up?",
    "cancel_here": "You don't need to do this yourself: I can stop it from renewing for you here, after checking its charges.",
    "history_unavailable": "I can't read your reload and charge records right now. Please try again in a moment.",
    "account_packages": "Packages on your line: {packages}.",
    "account_no_packages": "You have no packages on your line right now.",
    "package_item_data": "{name} ({status}, {data} data left)",
    "accepted_review": "Your case is queued for review (request ID {reference}). {status} I'll only give you a ticket number once one has actually been issued.",
    "ticket_issued": "Ticket number: {ticket}.",
    "synthetic_policy": "This is a demo policy for the simulation, not an official HUTCH rule:",
    # Shown as the customer's own message when they use a button or form instead of typing.
    "user_category": "{complaint}",
    "user_details": "{complaint}: details sent",
    "user_case_selection": "Switch to another case",
    "user_accept": "Yes, go ahead.",
    "user_decline": "No, don't make that change.",
    "off_topic": "I can only help with your HUTCH prepaid line: balance, reloads, data, packages, connection and services. What can I help you with?",
    "packages_login": "Please sign in so I can look at your data use and suggest a package. You can still ask me how packages work.",
    "packages_unavailable": "I couldn't load the packages right now. Please try again in a moment.",
    "package_selection_unavailable": "That offer is no longer available or cannot be activated for this line. Please review the current offers and try again.",
    "package_best_fit": "In the {period} you used {used} of data. The best fit is {name}: {data} for {validity}, {price}.",
    "package_alternatives": "Other options: {options}.",
    "package_offer_hint": "Tap 'Yes, go ahead' on the offer to activate it, or tell me which other package you'd like.",
    "package_choose": "Tell me which one you'd like and I'll prepare it for you to confirm.",
    "package_reload_first": "Your main balance is {balance}, so you would need to reload at least {reload} first.",
    "accepted_package": "Activation of {package} is requested. {status}",
    "activation_status": "Your {package} activation: {status}",
}

_ACTION_LABELS_EN: dict[ActionType, str] = {
    ActionType.DEACTIVATE_VAS: "stop future renewals",
    ActionType.SEND_SETTINGS_INSTRUCTIONS: "send settings instructions",
    ActionType.CREATE_REVIEW_TICKET: "send this to our review team",
    ActionType.ACTIVATE_PACKAGE: "activate the package",
}

_COMPLAINT_LABELS_EN: dict[ComplaintType, str] = {
    ComplaintType.BALANCE_RECHARGE: "balance or recharge",
    ComplaintType.DATA_DEPLETION: "data",
    ComplaintType.CONNECTIVITY: "connection",
    ComplaintType.VAS_DISPUTE: "service charge",
    ComplaintType.PACKAGE_ACTIVATION: "package purchase",
}

# Faithful wording per operation state: pending is never described as done.
_OPERATION_STATUS_EN: dict[OperationStatus, str] = {
    OperationStatus.PENDING: "It has not been completed yet; I'll show the result once it's confirmed.",
    OperationStatus.RUNNING: "It is being processed and is not confirmed yet.",
    OperationStatus.SUCCEEDED: "It has been completed and confirmed.",
    OperationStatus.FAILED: "It could not be completed. Nothing was changed by this request.",
    OperationStatus.UNKNOWN: "The result is not confirmed yet. Please don't submit it again; it is being checked.",
    OperationStatus.REVIEW_REQUIRED: "The result could not be confirmed automatically, so a person will review it.",
}

_MISSING_LABELS_EN: dict[str, str] = {
    "opening_balance": "the starting balance",
    "closing_balance": "the latest balance",
    "activation_evidence": "proof the service was activated",
    "usage_category": "the type of usage",
    # Codes sent by Harry's investigations (ResolveDev 9ab23d9); unknown codes use "unknown".
    "OPENING_SNAPSHOT_MISSING": "the starting balance",
    "CLOSING_SNAPSHOT_MISSING": "the latest balance",
    "QUOTA_OPENING_SNAPSHOT_MISSING": "the starting data balance",
    "QUOTA_CLOSING_SNAPSHOT_MISSING": "the latest data balance",
    "QUOTA_BUCKETS_MISSING": "your data bundle records",
    "CONSUMPTION_USAGE_RECORD_MISSING": "some data usage records",
    "USAGE_CONSUMPTION_ENTRY_MISSING": "some data usage records",
    "FRESH_SERVICE_CHECK_MISSING": "a recent network check for your line",
    "RECHARGE_RECORD_MISSING": "the recharge record",
    "VAS_ACTIVATION_EVIDENCE_MISSING": "proof the service was activated",
    "POSTING_REVERSAL_ORIGINAL_MISSING": "the original of a reversed charge",
    "QUOTA_REVERSAL_ORIGINAL_MISSING": "the original of a reversed data entry",
    "unknown": "some records",
}

_DELIVERY_EN: dict[DeliveryState, str] = {
    DeliveryState.PENDING: "The handoff to the review team is still pending; no ticket number has been issued yet.",
    DeliveryState.DELIVERED: "The review team has received it.",
    DeliveryState.FAILED: "The handoff could not be delivered yet, so it will be retried. Your request is still recorded.",
    DeliveryState.REVIEW_REQUIRED: "The handoff needs a person to check it. Your request is still recorded.",
}

_CASE_STATUS_EN = {
    "OPEN": "open",
    "AWAITING_CUSTOMER": "waiting for more information",
    "ACTION_PENDING": "waiting for an action to finish",
    "REVIEW_REQUIRED": "waiting for a person to review it",
    "RESOLVED": "resolved",
}

ENGLISH: dict[str, dict[str, str]] = {
    "strings": _EN,
    "action_labels": {k.value: v for k, v in _ACTION_LABELS_EN.items()},
    "complaint_labels": {k.value: v for k, v in _COMPLAINT_LABELS_EN.items()},
    "operation_status": {k.value: v for k, v in _OPERATION_STATUS_EN.items()},
    "missing_labels": _MISSING_LABELS_EN,
    "delivery": {k.value: v for k, v in _DELIVERY_EN.items()},
    "case_status": _CASE_STATUS_EN,
}
# Internal terms and voice prompts without reviewed translations remain English.
NOT_LOCALIZED = frozenset({
    "default_escalation_reason", "package_selection_unavailable", "PACKAGE_ACTIVATION",
    "offer_action_voice", "other_options_voice", "offer_still_open_voice", "confirm_prompt_voice",
    "offer_locked_voice", "anything_else_voice", "call_goodbye_voice",
    "ledger_lines", "faq_none_case",  # English until the SI/TA drafts gain a reviewed wording
})
LOCALES_DIR = Path(__file__).with_name("locales")
REVIEWED = "REVIEWED"
Locale = str  # "en", "si", "ta", "si-Latn"; Language members are valid locale codes
_LATIN_SCRIPTS = frozenset({"LATIN", "MIXED"})


def locale_for(language: Language, script: str | None = None) -> Locale:
    """Locale for replies: romanized when the customer types their language in Latin letters.

    Without a reviewed romanized file the reply falls back to English, never to the native
    script the customer did not use.
    """
    language = Language(language)
    if language is not Language.EN and script is not None and str(script) in _LATIN_SCRIPTS:
        return f"{language.value}-Latn"
    return language


@lru_cache(maxsize=None)
def load_locale(language: Language | Locale) -> dict:
    """The raw locale file (any status); empty for English or a missing file."""
    code = str(language)
    path = LOCALES_DIR / f"{code}.json"
    if code == Language.EN or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def active_sections(language: Language | Locale) -> dict[str, dict[str, str]]:
    locale = load_locale(language)
    return locale.get("sections", {}) if locale.get("status") == REVIEWED else {}


def locale_active(language: Language | Locale) -> bool:
    """True when replies for this locale come from a fluent-reviewed file."""
    return bool(active_sections(language))


def _get(section: str, key: str, language: Language | Locale) -> str:
    localized = active_sections(language).get(section, {})
    if key in localized and not (section == "strings" and key in NOT_LOCALIZED):
        return localized[key]
    return ENGLISH[section][key]


def text(key: str, language: Language | Locale, **values: object) -> str:
    return _get("strings", key, language).format(**values)


def action_label(action: ActionType, language: Language | Locale) -> str:
    return _get("action_labels", action.value, language)


def complaint_label(complaint: ComplaintType, language: Language | Locale) -> str:
    return _get("complaint_labels", complaint.value, language)


def operation_status_text(status: OperationStatus, language: Language | Locale) -> str:
    return _get("operation_status", status.value, language)


def handoff_text(handoff: Handoff, language: Language | Locale) -> str:
    """Delivery state, plus a ticket number only when the provider actually issued one."""
    parts = [_get("delivery", handoff.delivery_state.value, language)]
    if handoff.provider_ticket_id:
        parts.append(text("ticket_issued", language, ticket=handoff.provider_ticket_id))
    return " ".join(parts)


def case_status_label(status: str, language: Language | Locale = Language.EN) -> str:
    if status in ENGLISH["case_status"]:
        return _get("case_status", status, language)
    return status.lower().replace("_", " ")


# Display only: business data stays in UTC and minor units.
_COLOMBO = timezone(timedelta(hours=5, minutes=30))


def format_lkr(amount_minor: int) -> str:
    sign = "-" if amount_minor < 0 else ""
    rupees, cents = divmod(abs(amount_minor), 100)
    return f"{sign}LKR {rupees:,}.{cents:02d}"


# Plain words for Resolve's ledger term labels; unknown labels fall back to lower-case words.
LEDGER_LABELS_EN = {
    "RECHARGE": "recharge", "PACKAGE_RENEWAL": "package renewal", "PACKAGE_PURCHASE": "package purchase",
    "VAS_CHARGE": "value-added service charge", "RATED_USAGE": "usage charges",
}


def ledger_line(label: str, amount_minor: int) -> str:
    name = LEDGER_LABELS_EN.get(label, label.replace("_", " ").lower())
    return f"{name} {'+' if amount_minor > 0 else ''}{format_lkr(amount_minor)}"


def format_time(value: datetime) -> str:
    local = value.astimezone(_COLOMBO)
    return f"{local.day} {local:%b}, {local:%H:%M}"


def format_window(start: datetime, end: datetime) -> str:
    s, e = start.astimezone(_COLOMBO), end.astimezone(_COLOMBO)
    if s.date() == e.date() or (e - s <= timedelta(days=1) and e.hour == 0 and e.minute == 0):
        return f"{s.day} {s:%b}, {s:%H:%M}–{'24:00' if e.date() != s.date() else f'{e:%H:%M}'}"
    return f"{s.day} {s:%b} {s:%H:%M} to {e.day} {e:%b} {e:%H:%M}"


def missing_label(code: str, language: Language | Locale) -> str:
    """Plain words for a missing-evidence code; never shows a raw code to the customer."""
    return _get("missing_labels", code if code in ENGLISH["missing_labels"] else "unknown", language)
