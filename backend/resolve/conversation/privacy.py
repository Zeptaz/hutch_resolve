"""Mask identity and payment numbers in customer text before it reaches the model or storage.

The chat never needs them: Resolve already knows the signed-in line, and nothing here takes payments.
Masked: Sri Lankan NIC numbers (old 9 digits + V/X; new 12 digits when an NIC/ID word is near), card numbers
(13-19 digits passing the Luhn check, spaces/dashes allowed) and digits right after PIN/OTP/password/CVV.
Amounts, dates, phone numbers and recharge references are kept: the complaint needs them.
"""

from __future__ import annotations

import re

_OLD_NIC = re.compile(r"\b\d{9}[VvXx]\b")
_NEW_NIC = re.compile(r"(?i)(\b(?:nic|national id|id(?: number| no\.?)?|identity card)\b[^0-9]{0,15})(\d{12})\b")
_SECRET = re.compile(r"(?i)(\b(?:pin|otp|password|passcode|cvv|cvc)\b[^0-9]{0,12})(\d{3,8})\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        n = int(ch) * (2 if double else 1)
        total += n - 9 if n > 9 else n
        double = not double
    return total % 10 == 0


def _card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[card number removed]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)


def redact(text: str) -> str:
    text = _OLD_NIC.sub("[ID number removed]", text)
    text = _NEW_NIC.sub(lambda m: f"{m.group(1)}[ID number removed]", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}[secret removed]", text)
    return _CARD.sub(_card, text)
