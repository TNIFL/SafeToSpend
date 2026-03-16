from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def normalize_order_id(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 64 or not _SAFE_ID_RE.match(raw):
        return None
    return raw


def normalize_payment_key(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 128 or not _SAFE_ID_RE.match(raw):
        return None
    return raw


def normalize_transmission_id(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 128 or not _SAFE_ID_RE.match(raw):
        return None
    return raw


def build_event_hash(payload: Mapping[str, Any] | None) -> str:
    data = payload or {}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_idempotency_token(
    *,
    order_id: str | None = None,
    payment_key: str | None = None,
    transmission_id: str | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    tx = normalize_transmission_id(transmission_id)
    if tx:
        return ("transmission_id", tx)
    pkey = normalize_payment_key(payment_key)
    if pkey:
        return ("payment_key", pkey)
    oid = normalize_order_id(order_id)
    if oid:
        return ("order_id", oid)
    return ("event_hash", build_event_hash(event_payload))


def is_duplicate_by_keys(
    *,
    existing_order_ids: set[str] | None = None,
    existing_payment_keys: set[str] | None = None,
    existing_transmission_ids: set[str] | None = None,
    order_id: str | None = None,
    payment_key: str | None = None,
    transmission_id: str | None = None,
) -> bool:
    oid = normalize_order_id(order_id)
    pkey = normalize_payment_key(payment_key)
    tx = normalize_transmission_id(transmission_id)
    if oid and oid in (existing_order_ids or set()):
        return True
    if pkey and pkey in (existing_payment_keys or set()):
        return True
    if tx and tx in (existing_transmission_ids or set()):
        return True
    return False
