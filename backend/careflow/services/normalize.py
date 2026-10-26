"""Normalisation helpers for identifiers used in lookups and duplicate detection."""

from __future__ import annotations

import re

from careflow.errors import ValidationFailed

_PHONE_ALLOWED = re.compile(r"^\+?\d{7,15}$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_phone(phone: str) -> str:
    """Normalise to E.164-like form. Nigerian local numbers (0XXXXXXXXXX) become +234XXXXXXXXXX."""
    cleaned = re.sub(r"[\s\-().]", "", phone.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not _PHONE_ALLOWED.match(cleaned):
        raise ValidationFailed(details={"phone": ["Enter a valid phone number."]})
    if cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = "+234" + cleaned[1:]
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()
