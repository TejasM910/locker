"""Domain policies and helpers for Auth."""

import re
from fastapi import HTTPException


def normalize_phone(phone: str) -> str:
    """Normalize phone number to digits only, enforcing length requirement."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        raise HTTPException(400, "INVALID_PHONE")
    return digits
