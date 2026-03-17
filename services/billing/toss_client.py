from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

import requests
from flask import current_app, has_app_context

from .constants import PROVIDER_TOSS


class TossBillingConfigError(RuntimeError):
    pass


class TossBillingApiError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = str(code or "").strip() or None


@dataclass(frozen=True)
class TossBillingRegistrationPayload:
    provider: str
    client_key: str
    customer_key: str
    success_url: str
    fail_url: str


def _read_config_value(name: str) -> str:
    if has_app_context():
        direct = str(current_app.config.get(name) or "").strip()
        if direct:
            return direct
    return str(os.getenv(name) or "").strip()


def get_toss_client_key() -> str:
    value = _read_config_value("TOSS_PAYMENTS_CLIENT_KEY")
    if not value:
        raise TossBillingConfigError("토스 클라이언트 키가 설정되지 않았어요.")
    return value


def get_toss_secret_key() -> str:
    value = _read_config_value("TOSS_PAYMENTS_SECRET_KEY")
    if not value:
        raise TossBillingConfigError("토스 시크릿 키가 설정되지 않았어요.")
    return value


def get_toss_api_base_url() -> str:
    base = _read_config_value("TOSS_PAYMENTS_API_BASE_URL") or "https://api.tosspayments.com"
    return base.rstrip("/")


def _normalize_key_version(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return raw or "v1"


def get_active_billing_key_version() -> str:
    explicit = _read_config_value("BILLING_KEY_ACTIVE_VERSION")
    return _normalize_key_version(explicit)


def _versioned_secret_env_key(version: str) -> str:
    safe = "".join(ch for ch in str(version or "v1").upper() if ch.isalnum() or ch == "_")
    return f"BILLING_KEY_ENCRYPTION_SECRET_{safe}"


def get_billing_key_secret(*, key_version: str | None = None) -> str:
    version = _normalize_key_version(key_version or get_active_billing_key_version())
    versioned_key = _versioned_secret_env_key(version)
    versioned = _read_config_value(versioned_key)
    if versioned:
        return versioned
    fallback = _read_config_value("BILLING_KEY_ENCRYPTION_SECRET")
    if fallback:
        return fallback
    raise TossBillingConfigError(
        "BILLING_KEY_ENCRYPTION_SECRET 또는 버전별 비밀키가 설정되지 않았어요."
    )


def build_registration_payload(
    *,
    customer_key: str,
    success_url: str,
    fail_url: str,
) -> TossBillingRegistrationPayload:
    ckey = str(customer_key or "").strip()
    if not ckey:
        raise TossBillingConfigError("customerKey가 필요해요.")
    return TossBillingRegistrationPayload(
        provider=PROVIDER_TOSS,
        client_key=get_toss_client_key(),
        customer_key=ckey,
        success_url=str(success_url or "").strip(),
        fail_url=str(fail_url or "").strip(),
    )


def issue_billing_key(
    *,
    auth_key: str,
    customer_key: str,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    raw_auth_key = str(auth_key or "").strip()
    raw_customer_key = str(customer_key or "").strip()
    if not raw_auth_key or not raw_customer_key:
        raise TossBillingApiError("토스 등록 확인 정보가 부족해요.", code="missing_params")

    secret = get_toss_secret_key()
    auth = base64.b64encode(f"{secret}:".encode("utf-8")).decode("utf-8")
    url = f"{get_toss_api_base_url()}/v1/billing/authorizations/issue"
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    payload = {"authKey": raw_auth_key, "customerKey": raw_customer_key}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=max(3, int(timeout_sec or 10)))
    except requests.RequestException as e:
        raise TossBillingApiError("토스 결제수단 등록 확인 요청이 실패했어요.", code="network_error") from e

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not (200 <= int(resp.status_code or 0) < 300):
        fail_code = str((data or {}).get("code") or "").strip() or f"http_{resp.status_code}"
        msg = str((data or {}).get("message") or "").strip() or "토스 결제수단 등록 확인에 실패했어요."
        raise TossBillingApiError(msg, code=fail_code)

    billing_key = str((data or {}).get("billingKey") or "").strip()
    if not billing_key:
        raise TossBillingApiError("토스 응답에 billingKey가 없어요.", code="missing_billing_key")

    return {
        "billing_key": billing_key,
        "method": "card",
        "provider": PROVIDER_TOSS,
    }


def charge_billing_key(
    *,
    billing_key: str,
    customer_key: str,
    amount_krw: int,
    order_id: str,
    order_name: str,
    idempotency_key: str | None = None,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    raw_billing_key = str(billing_key or "").strip()
    raw_customer_key = str(customer_key or "").strip()
    raw_order_id = str(order_id or "").strip()
    raw_order_name = str(order_name or "").strip() or "쓸수있어 결제"
    amount = int(amount_krw or 0)
    if not raw_billing_key or not raw_customer_key or not raw_order_id:
        raise TossBillingApiError("결제 요청에 필요한 값이 부족해요.", code="missing_params")
    if amount <= 0:
        raise TossBillingApiError("결제 금액이 올바르지 않아요.", code="invalid_amount")

    secret = get_toss_secret_key()
    auth = base64.b64encode(f"{secret}:".encode("utf-8")).decode("utf-8")
    url = f"{get_toss_api_base_url()}/v1/billing/{raw_billing_key}"
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    idem = str(idempotency_key or "").strip()
    if idem:
        headers["Idempotency-Key"] = idem
    payload = {
        "customerKey": raw_customer_key,
        "amount": amount,
        "orderId": raw_order_id,
        "orderName": raw_order_name,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=max(3, int(timeout_sec or 10)))
    except requests.RequestException as e:
        raise TossBillingApiError("토스 자동결제 요청이 실패했어요.", code="network_error") from e

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not (200 <= int(resp.status_code or 0) < 300):
        fail_code = str((data or {}).get("code") or "").strip() or f"http_{resp.status_code}"
        msg = str((data or {}).get("message") or "").strip() or "토스 자동결제 승인에 실패했어요."
        raise TossBillingApiError(msg, code=fail_code)

    total_amount = None
    try:
        if "totalAmount" in data:
            total_amount = int(data.get("totalAmount") or 0)
    except Exception:
        total_amount = None
    return {
        "provider_status": str((data or {}).get("status") or "").strip().lower() or None,
        "order_id": str((data or {}).get("orderId") or "").strip() or raw_order_id,
        "payment_key": str((data or {}).get("paymentKey") or "").strip() or None,
        "total_amount": total_amount if total_amount is not None else amount,
        "currency": str((data or {}).get("currency") or "KRW").strip().upper() or "KRW",
        "approved_at": str((data or {}).get("approvedAt") or "").strip() or None,
        "fail_code": str((data or {}).get("code") or "").strip() or None,
        "fail_message": str((data or {}).get("message") or "").strip() or None,
    }


def fetch_payment_snapshot(
    *,
    order_id: str | None = None,
    payment_key: str | None = None,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    key = str(payment_key or "").strip()
    oid = str(order_id or "").strip()
    if not key and not oid:
        raise TossBillingApiError("orderId 또는 paymentKey가 필요해요.", code="missing_params")

    secret = get_toss_secret_key()
    auth = base64.b64encode(f"{secret}:".encode("utf-8")).decode("utf-8")
    base_url = get_toss_api_base_url()
    if key:
        url = f"{base_url}/v1/payments/{key}"
    else:
        url = f"{base_url}/v1/payments/orders/{oid}"
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=max(3, int(timeout_sec or 10)))
    except requests.RequestException as e:
        raise TossBillingApiError("토스 결제 상태 조회에 실패했어요.", code="network_error") from e

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not (200 <= int(resp.status_code or 0) < 300):
        fail_code = str((data or {}).get("code") or "").strip() or f"http_{resp.status_code}"
        msg = str((data or {}).get("message") or "").strip() or "토스 결제 상태 조회에 실패했어요."
        raise TossBillingApiError(msg, code=fail_code)

    total_amount = None
    try:
        if "totalAmount" in data:
            total_amount = int(data.get("totalAmount") or 0)
    except Exception:
        total_amount = None

    return {
        "provider_status": str((data or {}).get("status") or "").strip().lower() or None,
        "order_id": str((data or {}).get("orderId") or "").strip() or None,
        "payment_key": str((data or {}).get("paymentKey") or "").strip() or None,
        "total_amount": total_amount,
        "currency": str((data or {}).get("currency") or "").strip().upper() or None,
        "approved_at": str((data or {}).get("approvedAt") or "").strip() or None,
        "fail_code": str((data or {}).get("code") or "").strip() or None,
        "fail_message": str((data or {}).get("message") or "").strip() or None,
    }


class XorHmacCipher:
    """
    외부 암호화 라이브러리 없이 billingKey 저장 시 평문 저장을 피하기 위한 최소 구현.
    형식: base64url( nonce(16) + ciphertext + mac(16) )
    """

    def __init__(self, secret: str):
        raw = str(secret or "").strip()
        if len(raw) < 16:
            raise TossBillingConfigError("BILLING_KEY_ENCRYPTION_SECRET는 16자 이상이어야 해요.")
        self._key = hashlib.sha256(raw.encode("utf-8")).digest()

    def _stream(self, nonce: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            block = hmac.new(self._key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:length])

    def encrypt(self, plain_text: str) -> str:
        raw = str(plain_text or "").encode("utf-8")
        nonce = os.urandom(16)
        stream = self._stream(nonce, len(raw))
        cipher = bytes(a ^ b for a, b in zip(raw, stream))
        mac = hmac.new(self._key, nonce + cipher, hashlib.sha256).digest()[:16]
        token = base64.urlsafe_b64encode(nonce + cipher + mac).decode("utf-8")
        return token

    def decrypt(self, cipher_text: str) -> str:
        blob = base64.urlsafe_b64decode(str(cipher_text or "").encode("utf-8"))
        if len(blob) < 33:
            raise TossBillingConfigError("복호화할 수 없는 billingKey 암호문 형식이에요.")
        nonce = blob[:16]
        mac = blob[-16:]
        cipher = blob[16:-16]
        expected_mac = hmac.new(self._key, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac, expected_mac):
            raise TossBillingConfigError("billingKey 암호문 무결성 검증에 실패했어요.")
        stream = self._stream(nonce, len(cipher))
        plain = bytes(a ^ b for a, b in zip(cipher, stream))
        return plain.decode("utf-8")


def build_billing_key_cipher() -> XorHmacCipher:
    secret = get_billing_key_secret()
    return XorHmacCipher(secret)


def build_billing_key_cipher_for_version(key_version: str | None) -> XorHmacCipher:
    secret = get_billing_key_secret(key_version=key_version)
    return XorHmacCipher(secret)
