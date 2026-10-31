"""Security and data masking utilities.

Sanitizes credentials, bearer tokens, API keys, Aadhaar numbers,
and credit card numbers before parsing, persistence, or logging.
"""
from __future__ import annotations

import re

_KEYS = (
    r"(?:pass(?:word|wd)?|pwd|passcode|secret|token|api[_-]?key|"
    r"access[_-]?key|client[_-]?secret|auth(?:orization)?|otp|pin|cvv)"
)

_KV = re.compile(rf"(?i)(?<![A-Za-z0-9_])({_KEYS})(\s*[:=]\s*)(?!\*{{3,}})(\"[^\"]*\"|'[^']*'|\S+)")
_IS = re.compile(rf"(?i)(?<![A-Za-z0-9_])({_KEYS})(\s+(?:is|was|=>)\s+)(?!\*{{3,}})(\S+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]{8,}=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}\b")
_VENDOR = re.compile(r"\b(?:xox[abprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})\b")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def mask_text(text: str, mask_emails: bool = False) -> tuple[str, int]:
    """Return (masked_text, number_of_masked_values)."""
    count = 0

    def kv(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}****"

    def const(label: str):
        def _r(m: re.Match) -> str:
            nonlocal count
            count += 1
            return label
        return _r

    def card(m: re.Match) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            count += 1
            return "[CARD-MASKED]"
        return m.group(0)

    def bearer(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)} ****"

    text = _BEARER.sub(bearer, text)
    text = _KV.sub(kv, text)
    text = _IS.sub(kv, text)
    text = _JWT.sub(const("[TOKEN-MASKED]"), text)
    text = _VENDOR.sub(const("[TOKEN-MASKED]"), text)
    text = _CARD.sub(card, text)
    text = _AADHAAR.sub(const("[ID-MASKED]"), text)
    if mask_emails:
        text = _EMAIL.sub(const("[EMAIL-MASKED]"), text)
    return text, count
