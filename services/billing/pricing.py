from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from .constants import (
    ADDON_ACCOUNT_SLOT_PRICE_KRW,
    BASIC_PRICE_KRW,
    PLAN_BASIC,
    PLAN_PRO,
    PRO_PRICE_KRW,
)

from .constants import SUB_STATUS_ACTIVE, SUB_STATUS_GRACE_STARTED


def _to_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _get_field(src: Any, name: str, default: Any = None) -> Any:
    if isinstance(src, Mapping):
        return src.get(name, default)
    return getattr(src, name, default)


def calculate_addon_proration(
    *,
    anchor: date | datetime | None,
    current_period_end: date | datetime | None,
    quantity: int,
    unit_price_krw: int,
    as_of: date | datetime | None = None,
) -> int:
    qty = int(quantity or 0)
    unit_price = int(unit_price_krw or 0)
    if qty <= 0 or unit_price <= 0:
        return 0

    anchor_dt = _to_datetime(anchor)
    end_dt = _to_datetime(current_period_end)
    now_dt = _to_datetime(as_of) or datetime.now(timezone.utc)
    if not anchor_dt or not end_dt:
        raise ValueError("일할 계산을 위해 anchor/current_period_end 값이 필요해요.")
    if end_dt <= anchor_dt:
        raise ValueError("current_period_end는 billing anchor 이후여야 해요.")
    if now_dt >= end_dt:
        return 0

    base_amount = Decimal(unit_price * qty)
    total_secs = Decimal((end_dt - anchor_dt).total_seconds())
    remain_secs = Decimal((end_dt - max(now_dt, anchor_dt)).total_seconds())
    if total_secs <= 0:
        return 0
    ratio = max(Decimal("0"), min(Decimal("1"), remain_secs / total_secs))
    prorated = (base_amount * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(prorated))


def derive_next_billing_amount(
    subscription: Any,
    items: Iterable[Any],
    *,
    at: date | datetime | None = None,
) -> int:
    at_dt = _to_datetime(at) or datetime.now(timezone.utc)
    total = 0
    for item in items or []:
        status = str(_get_field(item, "status", "") or "").lower()
        if status not in {"active", "pending"}:
            continue
        effective_from = _to_datetime(_get_field(item, "effective_from"))
        effective_to = _to_datetime(_get_field(item, "effective_to"))
        if effective_from and at_dt < effective_from:
            continue
        if effective_to and at_dt >= effective_to:
            continue

        amount = _get_field(item, "amount_krw")
        if amount is None:
            quantity = int(_get_field(item, "quantity", 0) or 0)
            unit_price = int(_get_field(item, "unit_price_krw", 0) or 0)
            amount = quantity * unit_price
        total += max(0, int(amount or 0))
    return max(0, int(total))


def is_subscription_in_grace(
    subscription: Any = None,
    *,
    status: str | None = None,
    grace_until: date | datetime | None = None,
    now: date | datetime | None = None,
) -> bool:
    resolved_status = str(status or _get_field(subscription, "status", "") or "").lower()
    resolved_grace_until = _to_datetime(grace_until or _get_field(subscription, "grace_until"))
    now_dt = _to_datetime(now) or datetime.now(timezone.utc)
    if not resolved_grace_until:
        return False
    if resolved_status not in {SUB_STATUS_GRACE_STARTED, SUB_STATUS_ACTIVE}:
        return False
    return now_dt < resolved_grace_until


def should_transition_to_past_due(
    subscription: Any = None,
    *,
    status: str | None = None,
    grace_until: date | datetime | None = None,
    now: date | datetime | None = None,
) -> bool:
    resolved_status = str(status or _get_field(subscription, "status", "") or "").lower()
    if resolved_status == "past_due":
        return False
    resolved_grace_until = _to_datetime(grace_until or _get_field(subscription, "grace_until"))
    now_dt = _to_datetime(now) or datetime.now(timezone.utc)
    if not resolved_grace_until:
        return False
    if resolved_status not in {SUB_STATUS_GRACE_STARTED, SUB_STATUS_ACTIVE}:
        return False
    return now_dt >= resolved_grace_until


def _round_krw(value: Decimal | float | int) -> int:
    if isinstance(value, Decimal):
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_initial_subscription_amount(*, target_plan_code: str) -> int:
    code = str(target_plan_code or "").strip().lower()
    if code == PLAN_BASIC:
        return int(BASIC_PRICE_KRW)
    if code == PLAN_PRO:
        return int(PRO_PRICE_KRW)
    raise ValueError("최초 구독 대상 플랜이 올바르지 않아요.")


def calculate_upgrade_full_charge_amount(*, current_plan_code: str, target_plan_code: str) -> int:
    current = str(current_plan_code or "").strip().lower()
    target = str(target_plan_code or "").strip().lower()
    if current != PLAN_BASIC or target != PLAN_PRO:
        raise ValueError("지원하지 않는 업그레이드 요청이에요.")
    return int(PRO_PRICE_KRW)


def calculate_addon_proration_amount(
    *,
    anchor: date | datetime,
    current_period_end: date | datetime,
    quantity: int,
    as_of: date | datetime | None = None,
) -> int:
    amount = calculate_addon_proration(
        anchor=anchor,
        current_period_end=current_period_end,
        quantity=int(quantity or 0),
        unit_price_krw=int(ADDON_ACCOUNT_SLOT_PRICE_KRW),
        as_of=as_of,
    )
    return max(0, _round_krw(amount))


def build_checkout_pricing_snapshot(
    *,
    operation_type: str,
    amount_krw: int,
    currency: str = "KRW",
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    current_plan_code: str | None = None,
    current_plan_status: str | None = None,
    subscription_id: int | None = None,
    billing_anchor_at: date | datetime | None = None,
    current_period_end: date | datetime | None = None,
    existing_addon_quantity: int = 0,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "operation_type": str(operation_type or "").strip().lower(),
        "amount_krw": int(max(0, int(amount_krw or 0))),
        "currency": str(currency or "KRW").strip().upper() or "KRW",
        "target_plan_code": (str(target_plan_code or "").strip().lower() or None),
        "addon_quantity": (None if addon_quantity is None else int(max(0, int(addon_quantity or 0)))),
        "current_plan_code": (str(current_plan_code or "").strip().lower() or None),
        "current_plan_status": (str(current_plan_status or "").strip().lower() or None),
        "subscription_id": (None if not subscription_id else int(subscription_id)),
        "existing_addon_quantity": int(max(0, int(existing_addon_quantity or 0))),
    }
    if billing_anchor_at is not None:
        dt = _to_datetime(billing_anchor_at)
        snapshot["billing_anchor_at"] = dt.isoformat() if dt else None
    if current_period_end is not None:
        dt = _to_datetime(current_period_end)
        snapshot["current_period_end"] = dt.isoformat() if dt else None
    if (
        snapshot["operation_type"] == "addon_proration"
        and snapshot.get("addon_quantity") is not None
    ):
        snapshot["unit_price_krw"] = int(ADDON_ACCOUNT_SLOT_PRICE_KRW)
        qty = int(snapshot["addon_quantity"] or 0)
        snapshot["monthly_amount_after_cycle_krw"] = int(ADDON_ACCOUNT_SLOT_PRICE_KRW) * qty
    return snapshot


def calculate_subscription_cycle_amount(
    *,
    subscription: Any,
    items: Iterable[Any],
    cycle_at: date | datetime | None = None,
) -> dict[str, Any]:
    """
    특정 청구 주기 시점(cycle_at)에 유효한 구독 아이템만 포함해 정기 청구 금액을 계산한다.
    """
    resolved_cycle_at = (
        _to_datetime(cycle_at)
        or _to_datetime(_get_field(subscription, "current_period_end"))
        or _to_datetime(_get_field(subscription, "next_billing_at"))
        or datetime.now(timezone.utc)
    )
    if resolved_cycle_at is None:
        resolved_cycle_at = datetime.now(timezone.utc)

    effective_rows: list[Any] = []
    for item in items or []:
        status = str(_get_field(item, "status", "") or "").strip().lower()
        if status not in {"active", "pending"}:
            continue
        effective_from = _to_datetime(_get_field(item, "effective_from"))
        effective_to = _to_datetime(_get_field(item, "effective_to"))
        if effective_from and resolved_cycle_at < effective_from:
            continue
        if effective_to and resolved_cycle_at >= effective_to:
            continue
        effective_rows.append(item)

    def _row_amount(row: Any) -> int:
        amount = _get_field(row, "amount_krw")
        if amount is None:
            quantity = int(_get_field(row, "quantity", 0) or 0)
            unit_price = int(_get_field(row, "unit_price_krw", 0) or 0)
            amount = quantity * unit_price
        return max(0, int(amount or 0))

    plan_candidates: list[Any] = [
        row for row in effective_rows if str(_get_field(row, "item_type", "") or "").strip().lower() == "plan_base"
    ]
    plan_item = None
    if plan_candidates:
        plan_item = sorted(
            plan_candidates,
            key=lambda row: (
                _to_datetime(_get_field(row, "effective_from")) or datetime.min.replace(tzinfo=timezone.utc),
                int(_get_field(row, "id", 0) or 0),
            ),
            reverse=True,
        )[0]

    addon_rows = [
        row
        for row in effective_rows
        if str(_get_field(row, "item_type", "") or "").strip().lower() == "addon_account_slot"
    ]

    plan_amount_krw = _row_amount(plan_item) if plan_item is not None else 0
    addon_quantity = sum(max(0, int(_get_field(row, "quantity", 0) or 0)) for row in addon_rows)
    addon_amount_krw = sum(_row_amount(row) for row in addon_rows)
    total_amount_krw = max(0, int(plan_amount_krw + addon_amount_krw))

    return {
        "cycle_at": resolved_cycle_at.isoformat(),
        "plan_code": (str(_get_field(plan_item, "item_code", "") or "").strip().lower() or None),
        "plan_amount_krw": int(plan_amount_krw),
        "addon_quantity": int(addon_quantity),
        "addon_amount_krw": int(addon_amount_krw),
        "total_amount_krw": int(total_amount_krw),
        "currency": "KRW",
        "effective_item_ids": [int(_get_field(row, "id", 0) or 0) for row in effective_rows if _get_field(row, "id", 0)],
    }
