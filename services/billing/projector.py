from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from core.extensions import db
from core.time import utcnow
from domain.models import CheckoutIntent, EntitlementChangeLog, PaymentAttempt, Subscription, SubscriptionItem, User
from services.billing.constants import (
    ADDON_ACCOUNT_SLOT_PRICE_KRW,
    BASIC_PRICE_KRW,
    INTENT_TYPE_ADDON_PRORATION,
    INTENT_TYPE_INITIAL_SUBSCRIPTION,
    INTENT_TYPE_UPGRADE,
    PAYMENT_STATUS_CANCELED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_RECONCILED,
    PRO_PRICE_KRW,
)
from services.plan import PLAN_BASIC, PLAN_FREE, PLAN_PRO


VALID_PLAN_CODES = {PLAN_FREE, PLAN_BASIC, PLAN_PRO}


class BillingProjectorError(RuntimeError):
    pass


class BillingProjectorNotFound(BillingProjectorError):
    pass


@dataclass(frozen=True)
class EntitlementProjection:
    plan_code: str
    plan_status: str
    extra_account_slots: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_plan_from_code(plan_code: str) -> str:
    return "free" if str(plan_code or "").strip().lower() == PLAN_FREE else "pro"


def _normalize_plan_code(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if v in VALID_PLAN_CODES:
        return v
    return PLAN_FREE


def _normalize_plan_status(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if v in {"active", "inactive", "canceled", "past_due"}:
        return v
    return "active"


def _normalize_slots(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _current_user_snapshot(user: User) -> dict[str, Any]:
    return {
        "plan_code": _normalize_plan_code(getattr(user, "plan_code", None)),
        "plan_status": _normalize_plan_status(getattr(user, "plan_status", None)),
        "extra_account_slots": _normalize_slots(getattr(user, "extra_account_slots", 0)),
    }


def _lock_user(user_pk: int) -> User:
    query = User.query.filter_by(id=int(user_pk))
    try:
        row = query.with_for_update().first()
    except Exception:
        row = query.first()
    if not row:
        raise BillingProjectorNotFound("사용자 정보를 찾을 수 없어요.")
    return row


def _lock_payment_attempt(payment_attempt_id: int) -> PaymentAttempt:
    query = PaymentAttempt.query.filter_by(id=int(payment_attempt_id))
    try:
        row = query.with_for_update().first()
    except Exception:
        row = query.first()
    if not row:
        raise BillingProjectorNotFound("결제 시도 정보를 찾지 못했어요.")
    return row


def _lock_subscription(subscription_id: int) -> Subscription:
    query = Subscription.query.filter_by(id=int(subscription_id))
    try:
        row = query.with_for_update().first()
    except Exception:
        row = query.first()
    if not row:
        raise BillingProjectorNotFound("구독 정보를 찾지 못했어요.")
    return row


def _lock_checkout_intent(intent_id: int) -> CheckoutIntent | None:
    iid = int(intent_id or 0)
    if iid <= 0:
        return None
    query = CheckoutIntent.query.filter_by(id=iid)
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _load_subscription_items(subscription_id: int) -> list[SubscriptionItem]:
    return (
        SubscriptionItem.query.filter(SubscriptionItem.subscription_id == int(subscription_id))
        .order_by(SubscriptionItem.effective_from.desc(), SubscriptionItem.id.desc())
        .all()
    )


def _is_item_effective(item: SubscriptionItem, *, at: datetime) -> bool:
    status = str(item.status or "").strip().lower()
    if status not in {"active", "pending"}:
        return False
    start = item.effective_from
    end = item.effective_to
    if start and start > at:
        return False
    if end and end <= at:
        return False
    return True


def _find_effective_subscription_item(
    subscription_id: int,
    *,
    item_type: str,
    at: datetime,
) -> SubscriptionItem | None:
    rows = (
        SubscriptionItem.query.filter(SubscriptionItem.subscription_id == int(subscription_id))
        .filter(SubscriptionItem.item_type == str(item_type))
        .filter(SubscriptionItem.status.in_(("active", "pending")))
        .order_by(SubscriptionItem.effective_from.desc(), SubscriptionItem.id.desc())
        .all()
    )
    for row in rows:
        if _is_item_effective(row, at=at):
            return row
    return None


def _close_item(row: SubscriptionItem, *, now: datetime) -> None:
    row.status = "removed"
    row.effective_to = now
    row.updated_at = now
    db.session.add(row)


def _create_plan_item(
    *,
    subscription: Subscription,
    plan_code: str,
    now: datetime,
    reason: str,
    intent_id: int,
) -> SubscriptionItem:
    plan = str(plan_code or "").strip().lower()
    if plan == PLAN_BASIC:
        unit_price = int(BASIC_PRICE_KRW)
    elif plan == PLAN_PRO:
        unit_price = int(PRO_PRICE_KRW)
    else:
        unit_price = 0
    row = SubscriptionItem(
        subscription_id=int(subscription.id),
        user_pk=int(subscription.user_pk),
        item_type="plan_base",
        item_code=plan,
        quantity=1,
        unit_price_krw=unit_price,
        amount_krw=unit_price,
        status="active",
        effective_from=now,
        effective_to=None,
        snapshot_json={
            "created_by": "projector",
            "reason": reason,
            "intent_id": int(intent_id),
            "plan_code": plan,
        },
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    return row


def _ensure_plan_item_for_intent(
    *,
    subscription: Subscription,
    intent: CheckoutIntent,
    now: datetime,
    target_plan: str,
    reason: str,
) -> None:
    plan = str(target_plan or "").strip().lower()
    if plan not in {PLAN_BASIC, PLAN_PRO}:
        return
    active = _find_effective_subscription_item(int(subscription.id), item_type="plan_base", at=now)
    if active and str(active.item_code or "").strip().lower() == plan:
        return
    if active:
        _close_item(active, now=now)
    _create_plan_item(
        subscription=subscription,
        plan_code=plan,
        now=now,
        reason=reason,
        intent_id=int(intent.id),
    )


def _apply_addon_quantity_for_intent(
    *,
    subscription: Subscription,
    intent: CheckoutIntent,
    now: datetime,
) -> None:
    qty = int(intent.addon_quantity or 0)
    if qty <= 0:
        try:
            qty = int((intent.pricing_snapshot_json or {}).get("addon_quantity") or 0)
        except Exception:
            qty = 0
    if qty <= 0:
        return

    active = _find_effective_subscription_item(int(subscription.id), item_type="addon_account_slot", at=now)
    if active:
        next_qty = max(0, int(active.quantity or 0)) + qty
        active.quantity = next_qty
        active.unit_price_krw = int(ADDON_ACCOUNT_SLOT_PRICE_KRW)
        active.amount_krw = int(next_qty * ADDON_ACCOUNT_SLOT_PRICE_KRW)
        active.status = "active"
        active.updated_at = now
        snapshot = dict(active.snapshot_json or {})
        snapshot["last_intent_id"] = int(intent.id)
        snapshot["last_change"] = "addon_proration_increase"
        active.snapshot_json = snapshot
        db.session.add(active)
        return

    row = SubscriptionItem(
        subscription_id=int(subscription.id),
        user_pk=int(subscription.user_pk),
        item_type="addon_account_slot",
        item_code="addon_account_slot",
        quantity=qty,
        unit_price_krw=int(ADDON_ACCOUNT_SLOT_PRICE_KRW),
        amount_krw=int(qty * ADDON_ACCOUNT_SLOT_PRICE_KRW),
        status="active",
        effective_from=now,
        effective_to=None,
        snapshot_json={
            "created_by": "projector",
            "reason": "addon_proration_increase",
            "intent_id": int(intent.id),
            "added_quantity": qty,
        },
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)


def _set_checkout_intent_final_state(
    *,
    intent: CheckoutIntent,
    next_status: str,
    now: datetime,
) -> None:
    current = str(intent.status or "").strip().lower()
    target = str(next_status or "").strip().lower()
    if current == target:
        return
    intent.status = target
    if target in {"completed", "failed", "abandoned", "canceled"}:
        intent.completed_at = intent.completed_at or now
    intent.updated_at = now
    db.session.add(intent)


def _apply_checkout_intent_effects(
    *,
    attempt: PaymentAttempt,
    subscription: Subscription,
    now: datetime,
) -> None:
    intent = _lock_checkout_intent(int(attempt.checkout_intent_id or 0))
    if not intent:
        return

    attempt_status = str(attempt.status or "").strip().lower()
    intent_status = str(intent.status or "").strip().lower()
    if attempt_status == PAYMENT_STATUS_RECONCILED:
        # 이미 완료된 intent는 재반영하지 않는다(중복 projector 보호).
        if intent_status == "completed":
            return
        intent_type = str(intent.intent_type or "").strip().lower()
        if intent_type == INTENT_TYPE_INITIAL_SUBSCRIPTION:
            target_plan = str(intent.target_plan_code or "").strip().lower() or PLAN_BASIC
            _ensure_plan_item_for_intent(
                subscription=subscription,
                intent=intent,
                now=now,
                target_plan=target_plan,
                reason="initial_subscription_reconciled",
            )
        elif intent_type == INTENT_TYPE_UPGRADE:
            _ensure_plan_item_for_intent(
                subscription=subscription,
                intent=intent,
                now=now,
                target_plan=PLAN_PRO,
                reason="upgrade_reconciled",
            )
        elif intent_type == INTENT_TYPE_ADDON_PRORATION:
            _apply_addon_quantity_for_intent(subscription=subscription, intent=intent, now=now)
        _set_checkout_intent_final_state(intent=intent, next_status="completed", now=now)
        return

    if attempt_status in {PAYMENT_STATUS_FAILED, PAYMENT_STATUS_CANCELED}:
        if intent_status not in {"completed", "canceled", "abandoned"}:
            _set_checkout_intent_final_state(intent=intent, next_status="failed", now=now)


def derive_projection_from_subscription(subscription: Subscription, *, at: datetime | None = None) -> EntitlementProjection:
    now = at or _now()
    items = _load_subscription_items(int(subscription.id))

    effective_items = [item for item in items if _is_item_effective(item, at=now)]
    plan_item = next(
        (item for item in effective_items if str(item.item_type or "").strip().lower() == "plan_base"),
        None,
    )
    addon_qty = sum(
        _normalize_slots(item.quantity)
        for item in effective_items
        if str(item.item_type or "").strip().lower() == "addon_account_slot"
    )

    plan_code = _normalize_plan_code(str(plan_item.item_code or "") if plan_item else PLAN_FREE)
    sub_status = str(subscription.status or "").strip().lower()

    if sub_status == "canceled":
        return EntitlementProjection(plan_code=PLAN_FREE, plan_status="active", extra_account_slots=0)
    if sub_status == "pending_activation":
        return EntitlementProjection(plan_code=PLAN_FREE, plan_status="active", extra_account_slots=0)
    if sub_status == "past_due":
        return EntitlementProjection(plan_code=plan_code, plan_status="past_due", extra_account_slots=addon_qty)
    # grace_started / cancel_requested / pending_activation 포함: users.plan_status는 active 유지
    return EntitlementProjection(plan_code=plan_code, plan_status="active", extra_account_slots=addon_qty)


def _find_existing_log(user_pk: int, source_type: str, source_id: str) -> EntitlementChangeLog | None:
    return EntitlementChangeLog.query.filter_by(
        user_pk=int(user_pk),
        source_type=str(source_type),
        source_id=str(source_id),
    ).first()


def _apply_projection_with_log(
    *,
    user_pk: int,
    projection: EntitlementProjection,
    source_type: str,
    source_id: str,
    reason: str | None,
    commit: bool = True,
) -> dict[str, Any]:
    if _find_existing_log(user_pk, source_type, source_id):
        return {
            "ok": True,
            "applied": False,
            "duplicate": True,
            "source_type": str(source_type),
            "source_id": str(source_id),
        }

    user = _lock_user(int(user_pk))
    before = _current_user_snapshot(user)
    after = {
        "plan_code": _normalize_plan_code(projection.plan_code),
        "plan_status": _normalize_plan_status(projection.plan_status),
        "extra_account_slots": _normalize_slots(projection.extra_account_slots),
    }

    user.plan = _legacy_plan_from_code(after["plan_code"])
    if hasattr(user, "plan_code"):
        user.plan_code = after["plan_code"]
    if hasattr(user, "plan_status"):
        user.plan_status = after["plan_status"]
    if hasattr(user, "extra_account_slots"):
        user.extra_account_slots = after["extra_account_slots"]
    if hasattr(user, "plan_updated_at"):
        user.plan_updated_at = utcnow()
    db.session.add(user)

    log_row = EntitlementChangeLog(
        user_pk=int(user.id),
        source_type=str(source_type),
        source_id=str(source_id),
        before_json=before,
        after_json=after,
        reason=(str(reason or "").strip() or None),
        applied_at=_now(),
        created_at=_now(),
    )
    db.session.add(log_row)

    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except IntegrityError:
        db.session.rollback()
        if _find_existing_log(user_pk, source_type, source_id):
            return {
                "ok": True,
                "applied": False,
                "duplicate": True,
                "source_type": str(source_type),
                "source_id": str(source_id),
            }
        raise

    return {
        "ok": True,
        "applied": True,
        "duplicate": False,
        "user_pk": int(user.id),
        "source_type": str(source_type),
        "source_id": str(source_id),
        "before": before,
        "after": after,
    }


def apply_entitlement_from_payment_attempt(
    *,
    payment_attempt_id: int,
    source_type: str = "payment_attempt",
    source_id: str | None = None,
    reason: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    attempt = _lock_payment_attempt(int(payment_attempt_id))
    if not attempt.subscription_id:
        raise BillingProjectorError("구독 정보가 없는 결제 시도는 투영할 수 없어요.")
    subscription = _lock_subscription(int(attempt.subscription_id))
    now = _now()
    _apply_checkout_intent_effects(attempt=attempt, subscription=subscription, now=now)
    projection = derive_projection_from_subscription(subscription)
    sid = str(source_id or attempt.id)
    return _apply_projection_with_log(
        user_pk=int(attempt.user_pk),
        projection=projection,
        source_type=str(source_type),
        source_id=sid,
        reason=reason or f"payment_attempt:{attempt.id}",
        commit=commit,
    )


def apply_entitlement_from_subscription_state(
    *,
    subscription_id: int,
    source_type: str = "subscription_state",
    source_id: str | None = None,
    reason: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    subscription = _lock_subscription(int(subscription_id))
    projection = derive_projection_from_subscription(subscription)
    sid = str(source_id or subscription.id)
    return _apply_projection_with_log(
        user_pk=int(subscription.user_pk),
        projection=projection,
        source_type=str(source_type),
        source_id=sid,
        reason=reason or f"subscription_state:{subscription.id}",
        commit=commit,
    )


def reproject_entitlement_for_user(
    *,
    user_pk: int,
    source_type: str = "manual_reproject",
    source_id: str | None = None,
    reason: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    query = Subscription.query.filter(Subscription.user_pk == int(user_pk)).order_by(Subscription.id.desc())
    subscription = query.first()
    if not subscription:
        projection = EntitlementProjection(plan_code=PLAN_FREE, plan_status="active", extra_account_slots=0)
    else:
        projection = derive_projection_from_subscription(subscription)
    sid = str(source_id or f"user:{int(user_pk)}")
    return _apply_projection_with_log(
        user_pk=int(user_pk),
        projection=projection,
        source_type=str(source_type),
        source_id=sid,
        reason=reason or "manual_reproject",
        commit=commit,
    )
