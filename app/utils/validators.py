"""Validation and phone normalization helpers."""

import re

_CLEAN_CHARS_RE = re.compile(r"[\s\-\(\)\.]+")
_INDIAN_10_DIGIT_RE = re.compile(r"^[6-9]\d{9}$")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone_number(raw_phone: str) -> str:
    """Normalize 10-digit Indian or E.164 phone numbers to +<country><digits>."""
    if not isinstance(raw_phone, str):
        raise ValueError("Phone number must be a string.")

    cleaned = _CLEAN_CHARS_RE.sub("", raw_phone.strip())
    if not cleaned:
        raise ValueError("Phone number cannot be empty.")

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        if not _E164_RE.match(cleaned):
            raise ValueError(f"Invalid E.164 phone number '{raw_phone}'.")
        if cleaned.startswith("+91") and len(cleaned) != 13:
            raise ValueError(f"Invalid Indian phone number '{raw_phone}'.")
        return cleaned

    if not cleaned.isdigit():
        raise ValueError(f"Phone number '{raw_phone}' contains invalid characters.")

    if _INDIAN_10_DIGIT_RE.match(cleaned):
        return f"+91{cleaned}"
    if len(cleaned) == 11 and cleaned.startswith("0") and _INDIAN_10_DIGIT_RE.match(cleaned[1:]):
        return f"+91{cleaned[1:]}"
    if len(cleaned) == 12 and cleaned.startswith("91") and _INDIAN_10_DIGIT_RE.match(cleaned[2:]):
        return f"+91{cleaned[2:]}"

    raise ValueError(f"Invalid phone number '{raw_phone}'.")


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None