"""Read the customer's own claim from their words: an amount they say went missing, or the balance they see.

Deterministic and conservative: an amount counts only when it sits next to an explicit loss or balance
phrase, so "I recharged LKR 1000" is never read as a loss. These are claims to compare with the records,
never evidence. English phrasing only; Sinhala and Tamil complaints still pass their amount through
extraction (``amount_minor``).
"""

from __future__ import annotations

import re

# Thousands groups must be complete ("1,000"), so a list comma ("500, LKR 100") never joins two numbers.
_NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_CURRENCY = r"(?:lkr|rs\.?|rupees?)"
_AMOUNT = rf"(?:{_CURRENCY}\s*{_NUMBER}|{_NUMBER}\s*{_CURRENCY})"
_LOSS_VERB = r"(?:deducted|taken|cut|missing|gone|lost|disappeared|vanished|debited|reduced|charged|wiped)"

_LOSS_AFTER_AMOUNT = re.compile(
    rf"{_AMOUNT}\s+(?:\w+\s+){{0,3}}?(?:was|were|got|has been|have been|is|are|been)?\s*{_LOSS_VERB}\b", re.I)
_LOSS_BEFORE_AMOUNT = re.compile(
    rf"\b(?:deducted|lost|missing|took|taken|cut|debited|charged|reduced by|disappeared|lose|losing)\s+"
    rf"(?:me\s+|my\s+)?(?:about\s+|around\s+|almost\s+|nearly\s+|another\s+)?(?:{_CURRENCY}\s*)?{_NUMBER}\b(?!\s*(?:gb|mb|%|minutes?|mins?|sms))",
    re.I)
_BALANCE = re.compile(
    rf"\bbalance\s+(?:is|was|shows|showed|says|became|reads|now|went down to|dropped to|fell to)?\s*"
    rf"(?:now\s+)?(?:only\s+|just\s+)?(?:at\s+)?{_AMOUNT}", re.I)
_LEFT = re.compile(rf"\b(?:only|just)\s+{_AMOUNT}\s+(?:is\s+)?(?:left|remaining)\b|\bleft with\s+(?:only\s+|just\s+)?{_AMOUNT}", re.I)


def _minor(match: re.Match[str]) -> int | None:
    raw = next((group for group in match.groups() if group), None)
    if raw is None:
        return None
    try:
        value = round(float(raw.replace(",", "")) * 100)
    except ValueError:
        return None
    return value if 0 < value <= 9_007_199_254_740_991 else None


def parse_claims(text: str | None) -> tuple[int | None, int | None]:
    """(claimed_loss_minor, reported_balance_minor) found in the customer's text, else None for each."""
    if not text:
        return None, None
    loss = None
    for pattern in (_LOSS_AFTER_AMOUNT, _LOSS_BEFORE_AMOUNT):
        match = pattern.search(text)
        if match:
            loss = _minor(match)
            if loss is not None:
                break
    balance = None
    for pattern in (_BALANCE, _LEFT):
        match = pattern.search(text)
        if match:
            balance = _minor(match)
            if balance is not None:
                break
    return loss, balance
