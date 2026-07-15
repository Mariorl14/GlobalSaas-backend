"""Phone normalization for WhatsApp / SMS delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

_WHITESPACE_OR_SEP = re.compile(r"[\s\-\(\)]+")


@dataclass(frozen=True)
class PhoneNormalizationResult:
    ok: bool
    e164: str | None = None
    whatsapp_to: str | None = None
    error: str | None = None


def _clean_raw_phone(raw: str) -> str:
    return _WHITESPACE_OR_SEP.sub("", (raw or "").strip())


def normalize_phone_for_whatsapp(
    raw_phone: str | None,
    *,
    country_code: str | None = None,
    default_country_code: str | None = None,
) -> PhoneNormalizationResult:
    """
    Normalize a customer phone to E.164 and Twilio WhatsApp recipient format.
    Never raises — returns ok=False with a safe error message on failure.
    """
    if not raw_phone or not str(raw_phone).strip():
        return PhoneNormalizationResult(ok=False, error="missing_phone")

    cleaned = _clean_raw_phone(str(raw_phone))
    if not cleaned:
        return PhoneNormalizationResult(ok=False, error="missing_phone")

    region = (country_code or default_country_code or "").strip().upper() or None
    if region and len(region) != 2:
        region = None

    try:
        if cleaned.startswith("+"):
            parsed = phonenumbers.parse(cleaned, None)
        elif region:
            parsed = phonenumbers.parse(cleaned, region)
        else:
            return PhoneNormalizationResult(
                ok=False,
                error="invalid_phone_no_country",
            )
    except NumberParseException:
        return PhoneNormalizationResult(ok=False, error="invalid_phone_format")

    if not phonenumbers.is_possible_number(parsed):
        return PhoneNormalizationResult(ok=False, error="invalid_phone_format")
    if not phonenumbers.is_valid_number(parsed):
        return PhoneNormalizationResult(ok=False, error="invalid_phone_format")

    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    return PhoneNormalizationResult(
        ok=True,
        e164=e164,
        whatsapp_to=f"whatsapp:{e164}",
    )
