"""Deterministic reply templates used when no model is involved.

Only English is populated. Sinhala and Tamil fall back to English until wording
is written and checked by a fluent reviewer (T-04): the plan forbids claiming
native-language support that was not demonstrated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .dto import ActionType, ComplaintType, DeliveryState, Handoff, Language, OperationStatus

_EN: dict[str, str] = {
    "choose_complaint": "What would you like help with? Choose the closest option: balance or recharge, data running out, no connection, or a service you did not expect.",
    "login_required": "To look at your account, please sign in first. I can still answer general questions without signing in.",
    "complaint_details": "When did this happen? Please give the start and end of the time window (up to 30 days) and any amount you noticed.",
    "invalid_window_order": "The end of the time window must be after the start. Please check the dates.",
    "invalid_window_length": "I can look at up to 30 days at a time. Please choose a shorter window.",
    "evidence_partial": "Some records I need are not available yet ({missing}), so I can't confirm the full picture.",
    "evidence_conflicting": "The records don't agree with each other, so I can't give a final answer or change your account. A person needs to review this.",
    "no_findings": "I checked the available records but found nothing to report for that time window.",
    "offer_action": "I can {action} for {target}. What this means: {consequences} Do you want me to go ahead?",
    "multiple_actions": "There is more than one thing I can do here: {options}. Tell me which one you want.",
    "confirm_prompt": "Please use the Accept or Decline button to answer.",
    "confirm_prompt_voice": "Please say clearly whether you want me to go ahead: yes or no.",
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
    "checked_window": "I checked {window}.",
    "checked_default_window": "You didn't mention a time, so I checked {window}.",
    "rechecked": "I re-checked with the corrected details ({window}).",
    "offer_still_open": "My earlier offer is still open.",
    "no_pending_action": "There's nothing waiting for your confirmation right now.",
    "no_active_case": "There's no open request in this conversation yet. What would you like help with?",
    "case_status": "Your {complaint} request is {status}.",
    "receipt_ready": "A receipt is available for this request.",
    "human_needs_case": "I can pass this to a person with all the details. First, tell me what the problem is.",
    "default_escalation_reason": "Customer asked for a person to review this case.",
    "faq_none": "I don't have reviewed information on that yet. I can help with your balance, recharges, data, connection or service charges.",
    "guest_help": "I can answer general questions. To check your own account, please sign in first.",
    "account_balance": "Your {wallet} balance is {amount} (as of {as_of}).",
    "account_no_balance": "I couldn't find a balance for your account right now.",
    "account_incomplete": "Some account information may not be up to date.",
    "accepted_review": "Your case is queued for review (request ID {reference}). {status} I'll only give you a ticket number once one has actually been issued.",
    "ticket_issued": "Ticket number: {ticket}.",
    "synthetic_policy": "This is a demo policy for the simulation, not an official HUTCH rule:",
}

_ACTION_LABELS_EN: dict[ActionType, str] = {
    ActionType.DEACTIVATE_VAS: "stop future renewals",
    ActionType.SEND_SETTINGS_INSTRUCTIONS: "send settings instructions",
    ActionType.CREATE_REVIEW_TICKET: "send this to our review team",
}

_COMPLAINT_LABELS_EN: dict[ComplaintType, str] = {
    ComplaintType.BALANCE_RECHARGE: "balance or recharge",
    ComplaintType.DATA_DEPLETION: "data",
    ComplaintType.CONNECTIVITY: "connection",
    ComplaintType.VAS_DISPUTE: "service charge",
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
}

_DELIVERY_EN: dict[DeliveryState, str] = {
    DeliveryState.PENDING: "The handoff to the review team is still pending; no ticket number has been issued yet.",
    DeliveryState.DELIVERED: "The review team has received it.",
    DeliveryState.FAILED: "The handoff could not be delivered yet, so it will be retried. Your request is still recorded.",
    DeliveryState.REVIEW_REQUIRED: "The handoff needs a person to check it. Your request is still recorded.",
}

TEMPLATES: dict[Language, dict[str, str]] = {Language.EN: _EN}


def text(key: str, language: Language, **values: object) -> str:
    table = TEMPLATES.get(language) or _EN
    template = table.get(key) or _EN[key]
    return template.format(**values)


def action_label(action: ActionType, language: Language) -> str:
    return _ACTION_LABELS_EN[action]


def complaint_label(complaint: ComplaintType, language: Language) -> str:
    return _COMPLAINT_LABELS_EN[complaint]


def operation_status_text(status: OperationStatus, language: Language) -> str:
    return _OPERATION_STATUS_EN[status]


def handoff_text(handoff: Handoff, language: Language) -> str:
    """Delivery state, plus a ticket number only when the provider actually issued one."""
    parts = [_DELIVERY_EN[handoff.delivery_state]]
    if handoff.provider_ticket_id:
        parts.append(text("ticket_issued", language, ticket=handoff.provider_ticket_id))
    return " ".join(parts)


_CASE_STATUS_EN = {
    "OPEN": "open",
    "AWAITING_CUSTOMER": "waiting for more information",
    "ACTION_PENDING": "waiting for an action to finish",
    "REVIEW_REQUIRED": "waiting for a person to review it",
    "RESOLVED": "resolved",
}

# Display only: business data stays in UTC and minor units.
_COLOMBO = timezone(timedelta(hours=5, minutes=30))


def case_status_label(status: str) -> str:
    return _CASE_STATUS_EN.get(status, status.lower().replace("_", " "))


def format_lkr(amount_minor: int) -> str:
    sign = "-" if amount_minor < 0 else ""
    rupees, cents = divmod(abs(amount_minor), 100)
    return f"{sign}LKR {rupees:,}.{cents:02d}"


def format_time(value: datetime) -> str:
    local = value.astimezone(_COLOMBO)
    return f"{local.day} {local:%b}, {local:%H:%M}"


def format_window(start: datetime, end: datetime) -> str:
    s, e = start.astimezone(_COLOMBO), end.astimezone(_COLOMBO)
    if s.date() == e.date() or (e - s <= timedelta(days=1) and e.hour == 0 and e.minute == 0):
        return f"{s.day} {s:%b}, {s:%H:%M}–{'24:00' if e.date() != s.date() else f'{e:%H:%M}'}"
    return f"{s.day} {s:%b} {s:%H:%M} to {e.day} {e:%b} {e:%H:%M}"


def missing_label(code: str, language: Language) -> str:
    return _MISSING_LABELS_EN.get(code, code.replace("_", " "))
