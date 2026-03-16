from __future__ import annotations

import hashlib
from typing import Protocol

from services.sensitive_mask import mask_sensitive_numbers


class BillingSecurityError(ValueError):
    pass


class BillingKeyCipher(Protocol):
    def encrypt(self, plain_text: str) -> str: ...

    def decrypt(self, cipher_text: str) -> str: ...


def ensure_auth_key_not_persisted(payload: dict | None) -> None:
    data = payload or {}
    for key in data.keys():
        if str(key).lower() in {"authkey", "raw_authkey"}:
            raise BillingSecurityError("authKey는 일시 사용 후 폐기해야 해요.")


def hash_billing_key(billing_key: str) -> str:
    raw = str(billing_key or "").strip()
    if not raw:
        raise BillingSecurityError("billingKey가 비어 있어요.")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def encrypt_billing_key(billing_key: str, *, cipher: BillingKeyCipher | None) -> str:
    raw = str(billing_key or "").strip()
    if not raw:
        raise BillingSecurityError("billingKey가 비어 있어요.")
    if cipher is None:
        raise BillingSecurityError("billingKey 저장 전 암호화 구현체가 필요해요.")
    encrypted = str(cipher.encrypt(raw) or "").strip()
    if not encrypted:
        raise BillingSecurityError("billingKey 암호화 결과가 비어 있어요.")
    return encrypted


def mask_sensitive_log_text(text: str | None, *, max_len: int = 300) -> str:
    masked = mask_sensitive_numbers(str(text or ""))
    trimmed = masked.strip()
    if len(trimmed) <= max_len:
        return trimmed
    return f"{trimmed[: max_len - 1]}…"


def normalize_fail_message(message: str | None, *, max_len: int = 255) -> str:
    masked = mask_sensitive_log_text(message, max_len=max_len)
    return masked or "결제 처리 중 오류가 발생했어요."
