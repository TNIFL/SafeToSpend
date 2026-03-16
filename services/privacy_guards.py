from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_DIGIT_RE = re.compile(r"[^0-9]")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^(acct|id)_[0-9a-f]{12,64}$")


@dataclass(frozen=True, slots=True)
class SanitizedIdentifier:
    raw: str
    normalized_digits: str
    hashed: str
    masked: str
    last4: str
    storage_token: str


def _stringify(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_identifier_digits(value: object | None) -> str:
    return _DIGIT_RE.sub("", _stringify(value))


def hash_sensitive_identifier(value: object | None) -> str:
    raw = _stringify(value)
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_bank_identifier(value: object | None) -> str:
    raw = _stringify(value)
    digits = normalize_identifier_digits(raw)
    if len(digits) >= 6:
        return f"****{digits[-4:]}"
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"***{raw[-4:]}"


def redact_identifier_for_render(value: object | None) -> str:
    return mask_bank_identifier(value)


def make_identifier_storage_token(value: object | None, *, prefix: str = "acct") -> str:
    hashed = hash_sensitive_identifier(value)
    if not hashed:
        return ""
    return f"{prefix}_{hashed[:24]}"


def sanitize_account_like_value(value: object | None) -> SanitizedIdentifier:
    raw = _stringify(value)
    digits = normalize_identifier_digits(raw)
    hashed = hash_sensitive_identifier(digits or raw)
    last4 = digits[-4:] if len(digits) >= 4 else ""
    return SanitizedIdentifier(
        raw=raw,
        normalized_digits=digits,
        hashed=hashed,
        masked=mask_bank_identifier(digits or raw),
        last4=last4,
        storage_token=make_identifier_storage_token(digits or raw, prefix="acct"),
    )


def is_disallowed_identifier_storage(value: object | None) -> bool:
    raw = _stringify(value)
    if not raw:
        return False
    if _HEX64_RE.fullmatch(raw.lower()):
        return False
    if _TOKEN_RE.fullmatch(raw.lower()):
        return False
    digits = normalize_identifier_digits(raw)
    if len(digits) >= 6:
        return True
    if raw.startswith("****") or raw.startswith("***"):
        return False
    return False
