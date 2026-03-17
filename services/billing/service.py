from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from core.extensions import db
from domain.models import (
    BillingCustomer,
    BillingMethod,
    BillingMethodRegistrationAttempt,
    CheckoutIntent,
    PaymentAttempt,
    PaymentEvent,
    Subscription,
    SubscriptionItem,
)
from sqlalchemy.exc import IntegrityError

from .constants import (
    ADDON_ACCOUNT_SLOT_PRICE_KRW,
    ATTEMPT_TYPES,
    BASIC_PRICE_KRW,
    CHECKOUT_INTENT_DEFAULT_TTL_MINUTES,
    CHECKOUT_INTENT_STATUSES,
    CHECKOUT_INTENT_STATUS_CREATED,
    CHECKOUT_INTENT_TYPES,
    INTENT_TYPE_ADDON_PRORATION,
    INTENT_TYPE_INITIAL_SUBSCRIPTION,
    INTENT_TYPE_UPGRADE,
    PLAN_BASIC,
    PLAN_FREE,
    PLAN_PRO,
    PRO_PRICE_KRW,
    PROVIDER_TOSS,
    SUPPORTED_CURRENCIES,
)
from .idempotency import (
    build_event_hash,
    build_idempotency_token,
    normalize_order_id,
    normalize_payment_key,
    normalize_transmission_id,
)
from .pricing import derive_next_billing_amount, is_subscription_in_grace, should_transition_to_past_due
from .pricing import (
    build_checkout_pricing_snapshot,
    calculate_addon_proration_amount,
    calculate_initial_subscription_amount,
    calculate_upgrade_full_charge_amount,
)
from .security import (
    BillingKeyCipher,
    encrypt_billing_key,
    hash_billing_key,
    normalize_fail_message as normalize_fail_message_sec,
)
from .state_machine import (
    transition_payment_attempt_state,
    transition_subscription_state,
)
from services.plan import get_user_entitlements


class BillingDomainError(ValueError):
    pass


class BillingRegistrationError(BillingDomainError):
    pass


class BillingCheckoutValidationError(BillingDomainError):
    pass


@dataclass(frozen=True)
class BillingOperationDraft:
    user_pk: int
    provider: str
    order_id: str
    operation_type: str
    amount_krw: int
    currency: str
    created_at: datetime


@dataclass(frozen=True)
class CheckoutIntentDraft:
    user_pk: int
    intent_type: str
    target_plan_code: str | None
    addon_quantity: int | None
    currency: str
    amount_snapshot_krw: int
    pricing_snapshot_json: dict[str, Any]
    status: str
    requires_billing_method: bool
    billing_method_id: int | None
    related_subscription_id: int | None
    idempotency_key: str | None
    resume_token: str
    requested_at: datetime
    expires_at: datetime


_INTENT_TRANSITIONS: dict[str, set[str]] = {
    "created": {
        "registration_required",
        "ready_for_charge",
        "failed",
        "abandoned",
        "canceled",
    },
    "registration_required": {
        "ready_for_charge",
        "failed",
        "abandoned",
        "canceled",
    },
    "ready_for_charge": {"charge_started", "failed", "abandoned", "canceled"},
    "charge_started": {"completed", "failed", "ready_for_charge"},
    "completed": set(),
    "failed": {"ready_for_charge", "abandoned", "canceled"},
    "abandoned": {"ready_for_charge", "canceled"},
    "canceled": set(),
}


def generate_resume_token(prefix: str = "ckt") -> str:
    token = secrets.token_urlsafe(24)
    safe_prefix = str(prefix or "ckt").strip().lower()
    return f"{safe_prefix}_{token}"


def _normalize_checkout_intent_type(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if v not in CHECKOUT_INTENT_TYPES:
        raise BillingDomainError("지원하지 않는 checkout intent 타입이에요.")
    return v


def _normalize_checkout_status(value: str | None) -> str:
    v = str(value or CHECKOUT_INTENT_STATUS_CREATED).strip().lower()
    if v not in CHECKOUT_INTENT_STATUSES:
        raise BillingDomainError("지원하지 않는 checkout intent 상태예요.")
    return v


def _normalize_checkout_plan(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw not in {"free", "basic", "pro"}:
        raise BillingDomainError("지원하지 않는 대상 플랜이에요.")
    return raw


def transition_checkout_intent_status(current_status: str, next_status: str) -> str:
    current = _normalize_checkout_status(current_status)
    target = _normalize_checkout_status(next_status)
    if current == target:
        return target
    allowed = _INTENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise BillingDomainError(f"허용되지 않은 checkout intent 상태 전이예요: {current} -> {target}")
    return target


def build_checkout_intent_draft(
    *,
    user_pk: int,
    intent_type: str,
    amount_snapshot_krw: int,
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    currency: str = "KRW",
    pricing_snapshot_json: Mapping[str, Any] | None = None,
    status: str = CHECKOUT_INTENT_STATUS_CREATED,
    requires_billing_method: bool = True,
    billing_method_id: int | None = None,
    related_subscription_id: int | None = None,
    idempotency_key: str | None = None,
    resume_token: str | None = None,
    requested_at: datetime | None = None,
    ttl_minutes: int = CHECKOUT_INTENT_DEFAULT_TTL_MINUTES,
) -> CheckoutIntentDraft:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        raise BillingDomainError("유효한 사용자 ID가 필요해요.")
    normalized_type = _normalize_checkout_intent_type(intent_type)
    normalized_status = _normalize_checkout_status(status)
    normalized_plan = _normalize_checkout_plan(target_plan_code)
    amount = int(amount_snapshot_krw or 0)
    if amount < 0:
        raise BillingDomainError("결제 금액은 음수일 수 없어요.")
    qty: int | None = None
    if addon_quantity is not None:
        qty = int(addon_quantity)
        if qty < 0:
            raise BillingDomainError("추가 계좌 수량은 음수일 수 없어요.")

    curr = str(currency or "KRW").strip().upper()
    if curr not in SUPPORTED_CURRENCIES:
        raise BillingDomainError("지원하지 않는 통화예요.")
    billing_method_fk = None if billing_method_id is None else int(billing_method_id)
    subscription_fk = None if related_subscription_id is None else int(related_subscription_id)

    now = requested_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    expire_at = now + timedelta(minutes=max(5, int(ttl_minutes or CHECKOUT_INTENT_DEFAULT_TTL_MINUTES)))

    snapshot = dict(pricing_snapshot_json or {})
    if "amount_snapshot_krw" not in snapshot:
        snapshot["amount_snapshot_krw"] = amount
    if "intent_type" not in snapshot:
        snapshot["intent_type"] = normalized_type

    resume = str(resume_token or "").strip() or generate_resume_token()
    idem_key = str(idempotency_key or "").strip() or None

    return CheckoutIntentDraft(
        user_pk=user_id,
        intent_type=normalized_type,
        target_plan_code=normalized_plan,
        addon_quantity=qty,
        currency=curr,
        amount_snapshot_krw=amount,
        pricing_snapshot_json=snapshot,
        status=normalized_status,
        requires_billing_method=bool(requires_billing_method),
        billing_method_id=billing_method_fk,
        related_subscription_id=subscription_fk,
        idempotency_key=idem_key,
        resume_token=resume,
        requested_at=now,
        expires_at=expire_at,
    )


def create_checkout_intent(
    *,
    user_pk: int,
    intent_type: str,
    amount_snapshot_krw: int,
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    currency: str = "KRW",
    pricing_snapshot_json: Mapping[str, Any] | None = None,
    status: str = CHECKOUT_INTENT_STATUS_CREATED,
    requires_billing_method: bool = True,
    billing_method_id: int | None = None,
    related_subscription_id: int | None = None,
    idempotency_key: str | None = None,
    ttl_minutes: int = CHECKOUT_INTENT_DEFAULT_TTL_MINUTES,
    commit: bool = True,
) -> tuple[CheckoutIntent, bool]:
    draft = build_checkout_intent_draft(
        user_pk=user_pk,
        intent_type=intent_type,
        amount_snapshot_krw=amount_snapshot_krw,
        target_plan_code=target_plan_code,
        addon_quantity=addon_quantity,
        currency=currency,
        pricing_snapshot_json=pricing_snapshot_json,
        status=status,
        requires_billing_method=requires_billing_method,
        billing_method_id=billing_method_id,
        related_subscription_id=related_subscription_id,
        idempotency_key=idempotency_key,
        ttl_minutes=ttl_minutes,
    )
    row = CheckoutIntent(
        user_pk=draft.user_pk,
        intent_type=draft.intent_type,
        target_plan_code=draft.target_plan_code,
        addon_quantity=draft.addon_quantity,
        currency=draft.currency,
        amount_snapshot_krw=draft.amount_snapshot_krw,
        pricing_snapshot_json=draft.pricing_snapshot_json,
        status=draft.status,
        requires_billing_method=draft.requires_billing_method,
        billing_method_id=draft.billing_method_id,
        related_subscription_id=draft.related_subscription_id,
        idempotency_key=draft.idempotency_key,
        resume_token=draft.resume_token,
        requested_at=draft.requested_at,
        expires_at=draft.expires_at,
        created_at=draft.requested_at,
        updated_at=draft.requested_at,
    )
    db.session.add(row)
    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return row, True
    except IntegrityError:
        db.session.rollback()
        idem = str(draft.idempotency_key or "").strip()
        if idem:
            existing = _find_checkout_intent_by_user_idempotency(int(draft.user_pk), idem)
            if existing:
                return existing, False
        existing_by_resume = _find_checkout_intent_by_resume_token(str(draft.resume_token))
        if existing_by_resume:
            return existing_by_resume, False
        raise


def _find_checkout_intent_by_user_idempotency(user_pk: int, idempotency_key: str) -> CheckoutIntent | None:
    uid = int(user_pk or 0)
    key = str(idempotency_key or "").strip()
    if uid <= 0 or not key:
        return None
    return CheckoutIntent.query.filter_by(user_pk=uid, idempotency_key=key).first()


def _find_checkout_intent_by_resume_token(resume_token: str) -> CheckoutIntent | None:
    token = str(resume_token or "").strip()
    if not token:
        return None
    return CheckoutIntent.query.filter_by(resume_token=token).first()


def get_checkout_intent(intent_id: int, *, user_pk: int | None = None) -> CheckoutIntent | None:
    iid = int(intent_id or 0)
    if iid <= 0:
        return None
    query = CheckoutIntent.query.filter_by(id=iid)
    if user_pk is not None and int(user_pk or 0) > 0:
        query = query.filter_by(user_pk=int(user_pk))
    return query.first()


def get_checkout_intent_by_resume_token(resume_token: str, *, user_pk: int | None = None) -> CheckoutIntent | None:
    token = str(resume_token or "").strip()
    if not token:
        return None
    if user_pk is None:
        return _find_checkout_intent_by_resume_token(token)
    query = CheckoutIntent.query.filter_by(resume_token=token)
    if user_pk is not None and int(user_pk or 0) > 0:
        query = query.filter_by(user_pk=int(user_pk))
    return query.first()


def lock_checkout_intent(intent_id: int) -> CheckoutIntent | None:
    iid = int(intent_id or 0)
    if iid <= 0:
        return None
    query = CheckoutIntent.query.filter_by(id=iid)
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def lock_checkout_intent_by_resume_token(*, user_pk: int, resume_token: str) -> CheckoutIntent | None:
    uid = int(user_pk or 0)
    token = str(resume_token or "").strip()
    if uid <= 0 or not token:
        return None
    query = CheckoutIntent.query.filter_by(user_pk=uid, resume_token=token)
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def set_checkout_intent_status(
    *,
    intent: CheckoutIntent,
    next_status: str,
    commit: bool = True,
) -> CheckoutIntent:
    current = str(intent.status or "").strip().lower()
    target = transition_checkout_intent_status(current, next_status)
    if current == target:
        return intent
    now = datetime.now(timezone.utc)
    intent.status = target
    intent.updated_at = now
    if target in {"completed", "failed", "abandoned", "canceled"} and not intent.completed_at:
        intent.completed_at = now
    db.session.add(intent)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return intent


def _latest_active_subscription_for_user(user_pk: int) -> Subscription | None:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        return None
    return (
        Subscription.query.filter(Subscription.user_pk == user_id)
        .filter(
            Subscription.status.in_(
                (
                    "pending_activation",
                    "active",
                    "grace_started",
                    "cancel_requested",
                    "past_due",
                )
            )
        )
        .order_by(Subscription.id.desc())
        .first()
    )


def _current_addon_quantity(subscription_id: int, *, at: datetime | None = None) -> int:
    sid = int(subscription_id or 0)
    if sid <= 0:
        return 0
    now = at or datetime.now(timezone.utc)
    rows = (
        SubscriptionItem.query.filter(SubscriptionItem.subscription_id == sid)
        .filter(SubscriptionItem.item_type == "addon_account_slot")
        .filter(SubscriptionItem.status.in_(("active", "pending")))
        .all()
    )
    total = 0
    for row in rows:
        start = row.effective_from
        end = row.effective_to
        if start and start > now:
            continue
        if end and end <= now:
            continue
        total += max(0, int(row.quantity or 0))
    return int(total)


def resolve_checkout_pricing(
    *,
    user_pk: int,
    operation_type: str,
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        raise BillingCheckoutValidationError("유효한 사용자 정보를 찾지 못했어요.")
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    op = _normalize_checkout_intent_type(operation_type)
    ent = get_user_entitlements(user_id)
    current_plan_code = str(ent.plan_code or PLAN_FREE).strip().lower()
    current_plan_status = str(ent.plan_status or "active").strip().lower()
    subscription = _latest_active_subscription_for_user(user_id)
    subscription_status = str(getattr(subscription, "status", "") or "").strip().lower()

    if op == INTENT_TYPE_INITIAL_SUBSCRIPTION:
        target = _normalize_checkout_plan(target_plan_code)
        if current_plan_code != PLAN_FREE:
            raise BillingCheckoutValidationError("무료 플랜에서만 최초 구독을 시작할 수 있어요.")
        if target not in {PLAN_BASIC, PLAN_PRO}:
            raise BillingCheckoutValidationError("최초 구독은 베이직 또는 프로만 선택할 수 있어요.")
        amount_krw = calculate_initial_subscription_amount(target_plan_code=target)
        snapshot = build_checkout_pricing_snapshot(
            operation_type=op,
            amount_krw=amount_krw,
            target_plan_code=target,
            current_plan_code=current_plan_code,
            current_plan_status=current_plan_status,
        )
        return {
            "operation_type": op,
            "target_plan_code": target,
            "addon_quantity": None,
            "amount_krw": int(amount_krw),
            "currency": "KRW",
            "pricing_snapshot_json": snapshot,
            "related_subscription_id": None,
            "requires_billing_method": True,
        }

    if op == INTENT_TYPE_UPGRADE:
        target = _normalize_checkout_plan(target_plan_code) or PLAN_PRO
        if target != PLAN_PRO:
            raise BillingCheckoutValidationError("업그레이드는 프로 플랜으로만 진행할 수 있어요.")
        if current_plan_code != PLAN_BASIC:
            raise BillingCheckoutValidationError("업그레이드는 베이직 사용자만 진행할 수 있어요.")
        if current_plan_status in {"inactive", "canceled", "past_due"}:
            raise BillingCheckoutValidationError("현재 결제 상태에서는 업그레이드를 진행할 수 없어요.")
        if not subscription:
            raise BillingCheckoutValidationError("활성 구독 정보를 찾지 못했어요. 고객센터로 문의해 주세요.")
        if subscription_status in {"cancel_requested", "canceled", "past_due"}:
            raise BillingCheckoutValidationError("현재 구독 상태에서는 업그레이드를 진행할 수 없어요.")
        if subscription_status not in {"active", "grace_started"}:
            raise BillingCheckoutValidationError("업그레이드를 시작할 수 있는 구독 상태가 아니에요.")
        amount_krw = calculate_upgrade_full_charge_amount(
            current_plan_code=current_plan_code,
            target_plan_code=target,
        )
        snapshot = build_checkout_pricing_snapshot(
            operation_type=op,
            amount_krw=amount_krw,
            target_plan_code=target,
            current_plan_code=current_plan_code,
            current_plan_status=current_plan_status,
            subscription_id=int(subscription.id),
            billing_anchor_at=subscription.billing_anchor_at,
            current_period_end=subscription.current_period_end,
        )
        return {
            "operation_type": op,
            "target_plan_code": target,
            "addon_quantity": None,
            "amount_krw": int(amount_krw),
            "currency": "KRW",
            "pricing_snapshot_json": snapshot,
            "related_subscription_id": int(subscription.id),
            "requires_billing_method": True,
        }

    if op == INTENT_TYPE_ADDON_PRORATION:
        if current_plan_code not in {PLAN_BASIC, PLAN_PRO}:
            raise BillingCheckoutValidationError("추가 계좌 구매는 베이직 이상 플랜에서만 가능해요.")
        if current_plan_status in {"inactive", "canceled", "past_due"}:
            raise BillingCheckoutValidationError("현재 결제 상태에서는 추가 계좌를 구매할 수 없어요.")
        qty = int(addon_quantity or 0)
        if qty <= 0:
            raise BillingCheckoutValidationError("추가 계좌 수량은 1개 이상이어야 해요.")
        if not subscription:
            raise BillingCheckoutValidationError("활성 구독 정보를 찾지 못했어요. 고객센터로 문의해 주세요.")
        if subscription_status in {"cancel_requested", "canceled", "past_due"}:
            raise BillingCheckoutValidationError("현재 구독 상태에서는 추가 계좌를 구매할 수 없어요.")
        if subscription_status not in {"active", "grace_started"}:
            raise BillingCheckoutValidationError("추가 계좌를 시작할 수 있는 구독 상태가 아니에요.")
        anchor = subscription.billing_anchor_at or subscription.current_period_start
        period_end = subscription.current_period_end
        if not anchor or not period_end:
            raise BillingCheckoutValidationError("일할 계산에 필요한 구독 기간 정보를 찾지 못했어요.")
        amount_krw = calculate_addon_proration_amount(
            anchor=anchor,
            current_period_end=period_end,
            quantity=qty,
            as_of=now_dt,
        )
        existing_qty = _current_addon_quantity(int(subscription.id), at=now_dt)
        snapshot = build_checkout_pricing_snapshot(
            operation_type=op,
            amount_krw=amount_krw,
            addon_quantity=qty,
            current_plan_code=current_plan_code,
            current_plan_status=current_plan_status,
            subscription_id=int(subscription.id),
            billing_anchor_at=anchor,
            current_period_end=period_end,
            existing_addon_quantity=existing_qty,
        )
        snapshot["addon_unit_price_krw"] = int(ADDON_ACCOUNT_SLOT_PRICE_KRW)
        snapshot["addon_quantity_after_purchase"] = int(existing_qty + qty)
        snapshot["addon_amount_next_cycle_krw"] = int((existing_qty + qty) * ADDON_ACCOUNT_SLOT_PRICE_KRW)
        return {
            "operation_type": op,
            "target_plan_code": current_plan_code,
            "addon_quantity": int(qty),
            "amount_krw": int(amount_krw),
            "currency": "KRW",
            "pricing_snapshot_json": snapshot,
            "related_subscription_id": int(subscription.id),
            "requires_billing_method": True,
        }

    raise BillingCheckoutValidationError("지원하지 않는 결제 시작 요청이에요.")


def get_active_billing_method(*, user_pk: int, provider: str = PROVIDER_TOSS) -> BillingMethod | None:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        return None
    provider_name = str(provider or PROVIDER_TOSS).strip().lower() or PROVIDER_TOSS
    provider_aliases = {provider_name}
    if provider_name == PROVIDER_TOSS:
        # 레거시 데이터 호환: 과거에 저장된 provider 문자열이 남아 있을 수 있다.
        provider_aliases.update({"tosspayments", "toss_payments", "tosspayments_v2"})

    method = (
        BillingMethod.query.filter(BillingMethod.user_pk == user_id)
        .filter(BillingMethod.provider.in_(tuple(sorted(provider_aliases))))
        .filter(BillingMethod.status == "active")
        .order_by(BillingMethod.id.desc())
        .first()
    )
    if method:
        return method
    # provider 명이 다른 레거시 행이 남은 경우를 위한 최종 fallback
    return (
        BillingMethod.query.filter(BillingMethod.user_pk == user_id)
        .filter(BillingMethod.status == "active")
        .order_by(BillingMethod.id.desc())
        .first()
    )


def get_intent_bound_billing_method(*, user_pk: int, intent: CheckoutIntent) -> BillingMethod | None:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        return None
    method_id = int(getattr(intent, "billing_method_id", 0) or 0)
    if method_id <= 0:
        return None
    method = BillingMethod.query.filter_by(id=method_id, user_pk=user_id).first()
    if not method:
        return None
    if str(getattr(method, "status", "") or "").strip().lower() != "active":
        return None
    return method


def resolve_checkout_billing_method(*, user_pk: int, intent: CheckoutIntent) -> BillingMethod | None:
    """
    checkout intent에 결합된 결제수단을 우선 사용한다.
    결합된 결제수단이 무효(비활성/삭제/타 유저)일 때는 사용자의 active 결제수단으로 1회 fallback한다.
    """
    bound = get_intent_bound_billing_method(user_pk=user_pk, intent=intent)
    if bound:
        return bound
    return get_active_billing_method(user_pk=int(user_pk or 0))


def _intent_is_expired(intent: CheckoutIntent, *, now: datetime | None = None) -> bool:
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    expires = intent.expires_at
    if not expires:
        return False
    return expires <= now_dt


def _intent_is_open(intent: CheckoutIntent) -> bool:
    return str(intent.status or "").strip().lower() in {
        CHECKOUT_INTENT_STATUS_CREATED,
        "registration_required",
        "ready_for_charge",
        "charge_started",
    }


def find_reusable_checkout_intent(
    *,
    user_pk: int,
    operation_type: str,
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    now: datetime | None = None,
) -> CheckoutIntent | None:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        return None
    op = _normalize_checkout_intent_type(operation_type)
    plan = _normalize_checkout_plan(target_plan_code)
    qty = None if addon_quantity is None else int(addon_quantity or 0)
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    rows = (
        CheckoutIntent.query.filter(CheckoutIntent.user_pk == user_id)
        .filter(CheckoutIntent.intent_type == op)
        .order_by(CheckoutIntent.requested_at.desc(), CheckoutIntent.id.desc())
        .limit(8)
        .all()
    )
    for row in rows:
        if plan != (str(row.target_plan_code or "").strip().lower() or None):
            continue
        if qty != (None if row.addon_quantity is None else int(row.addon_quantity or 0)):
            continue
        if not _intent_is_open(row):
            continue
        if _intent_is_expired(row, now=now_dt):
            continue
        return row
    return None


def start_checkout_intent(
    *,
    user_pk: int,
    operation_type: str,
    target_plan_code: str | None = None,
    addon_quantity: int | None = None,
    idempotency_key: str | None = None,
    return_to: str | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    user_id = int(user_pk or 0)
    if user_id <= 0:
        raise BillingCheckoutValidationError("유효한 사용자 정보를 찾지 못했어요.")
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    op = _normalize_checkout_intent_type(operation_type)
    target = _normalize_checkout_plan(target_plan_code)
    qty = None if addon_quantity is None else int(addon_quantity or 0)

    existing = find_reusable_checkout_intent(
        user_pk=user_id,
        operation_type=op,
        target_plan_code=target,
        addon_quantity=qty,
        now=now_dt,
    )
    active_method = get_active_billing_method(user_pk=user_id)
    next_status = "ready_for_charge" if active_method else "registration_required"
    next_step = "confirm" if active_method else "registration"

    if existing:
        current_status = str(getattr(existing, "status", "") or "").strip().lower()
        is_protected_no_demote = current_status in {"charge_started", "charge_in_progress", "awaiting_reconcile"}
        is_ready_guarded = current_status == "ready_for_charge"
        bound_method = get_intent_bound_billing_method(user_pk=user_id, intent=existing)

        effective_method = active_method
        if bound_method:
            effective_method = bound_method

        if is_protected_no_demote:
            next_status = current_status
        elif is_ready_guarded and bound_method:
            next_status = "ready_for_charge"
        else:
            next_status = "ready_for_charge" if effective_method else "registration_required"

        next_step = "confirm" if next_status != "registration_required" else "registration"
        changed = False
        if existing.status != next_status:
            existing.status = next_status
            changed = True
        if effective_method and int(existing.billing_method_id or 0) != int(effective_method.id):
            existing.billing_method_id = int(effective_method.id)
            changed = True
        if return_to:
            snapshot = dict(existing.pricing_snapshot_json or {})
            if snapshot.get("return_to") != return_to:
                snapshot["return_to"] = str(return_to)
                existing.pricing_snapshot_json = snapshot
                changed = True
        if changed:
            existing.updated_at = now_dt
            db.session.add(existing)
            if commit:
                db.session.commit()
            else:
                db.session.flush()
        return {
            "intent": existing,
            "created": False,
            "next_step": next_step,
            "requires_registration": bool(next_status == "registration_required"),
            "pricing": dict(existing.pricing_snapshot_json or {}),
            "amount_krw": int(existing.amount_snapshot_krw or 0),
            "currency": str(existing.currency or "KRW"),
        }

    pricing = resolve_checkout_pricing(
        user_pk=user_id,
        operation_type=op,
        target_plan_code=target,
        addon_quantity=qty,
        now=now_dt,
    )
    pricing_snapshot = dict(pricing.get("pricing_snapshot_json") or {})
    if return_to:
        pricing_snapshot["return_to"] = str(return_to)
    intent, created = create_checkout_intent(
        user_pk=user_id,
        intent_type=op,
        amount_snapshot_krw=int(pricing.get("amount_krw") or 0),
        target_plan_code=(pricing.get("target_plan_code") or target),
        addon_quantity=pricing.get("addon_quantity"),
        currency=str(pricing.get("currency") or "KRW"),
        pricing_snapshot_json=pricing_snapshot,
        status=next_status,
        requires_billing_method=bool(pricing.get("requires_billing_method", True)),
        billing_method_id=(int(active_method.id) if active_method else None),
        related_subscription_id=pricing.get("related_subscription_id"),
        idempotency_key=idempotency_key,
        commit=commit,
    )
    return {
        "intent": intent,
        "created": created,
        "next_step": next_step,
        "requires_registration": not bool(active_method),
        "pricing": dict(pricing.get("pricing_snapshot_json") or {}),
        "amount_krw": int(pricing.get("amount_krw") or 0),
        "currency": str(pricing.get("currency") or "KRW"),
    }


def _map_intent_type_to_attempt_type(intent_type: str) -> str:
    normalized = _normalize_checkout_intent_type(intent_type)
    if normalized == INTENT_TYPE_INITIAL_SUBSCRIPTION:
        return "initial"
    if normalized == INTENT_TYPE_UPGRADE:
        return "upgrade_full_charge"
    if normalized == INTENT_TYPE_ADDON_PRORATION:
        return "addon_proration"
    raise BillingCheckoutValidationError("지원하지 않는 결제 타입이에요.")


def _ensure_initial_subscription_context(
    *,
    intent: CheckoutIntent,
    billing_customer_id: int,
    billing_method_id: int,
    now: datetime,
) -> int:
    existing_id = int(intent.related_subscription_id or 0)
    if existing_id > 0:
        return existing_id
    plan_code = _normalize_checkout_plan(intent.target_plan_code) or PLAN_BASIC
    if plan_code == PLAN_BASIC:
        unit_price = int(BASIC_PRICE_KRW)
    elif plan_code == PLAN_PRO:
        unit_price = int(PRO_PRICE_KRW)
    else:
        raise BillingCheckoutValidationError("최초 구독 대상 플랜이 올바르지 않아요.")

    sub = Subscription(
        user_pk=int(intent.user_pk),
        provider=PROVIDER_TOSS,
        billing_customer_id=int(billing_customer_id),
        billing_method_id=int(billing_method_id),
        status="pending_activation",
        billing_anchor_at=now,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        next_billing_at=now + timedelta(days=30),
        last_paid_at=None,
        grace_until=None,
        retry_count=0,
        last_failed_at=None,
        cancel_requested_at=None,
        cancel_effective_at=None,
        canceled_at=None,
        created_at=now,
        updated_at=now,
    )
    db.session.add(sub)
    db.session.flush()

    item = SubscriptionItem(
        subscription_id=int(sub.id),
        user_pk=int(intent.user_pk),
        item_type="plan_base",
        item_code=plan_code,
        quantity=1,
        unit_price_krw=unit_price,
        amount_krw=unit_price,
        status="active",
        effective_from=now,
        effective_to=None,
        snapshot_json={
            "created_by": "checkout_initial",
            "intent_id": int(intent.id),
            "plan_code": plan_code,
            "unit_price_krw": unit_price,
        },
        created_at=now,
        updated_at=now,
    )
    db.session.add(item)
    intent.related_subscription_id = int(sub.id)
    intent.updated_at = now
    db.session.add(intent)
    db.session.flush()
    return int(sub.id)


def _load_payment_attempt_by_intent(intent_id: int) -> PaymentAttempt | None:
    iid = int(intent_id or 0)
    if iid <= 0:
        return None
    return (
        PaymentAttempt.query.filter(PaymentAttempt.checkout_intent_id == iid)
        .order_by(PaymentAttempt.id.desc())
        .first()
    )


def confirm_checkout_intent_charge(
    *,
    user_pk: int,
    resume_token: str,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    user_id = int(user_pk or 0)
    token = str(resume_token or "").strip()
    if user_id <= 0 or not token:
        raise BillingCheckoutValidationError("결제 확인 정보가 올바르지 않아요.")

    now = datetime.now(timezone.utc)
    intent = lock_checkout_intent_by_resume_token(user_pk=user_id, resume_token=token)
    if not intent:
        raise BillingCheckoutValidationError("결제 의도 정보를 찾지 못했어요.")
    if _intent_is_expired(intent):
        set_checkout_intent_status(intent=intent, next_status="abandoned", commit=commit)
        raise BillingCheckoutValidationError("결제 요청이 만료되었어요. 다시 시작해 주세요.")

    current_status = str(intent.status or "").strip().lower()
    if current_status == "completed":
        existing = _load_payment_attempt_by_intent(int(intent.id))
        return {
            "ok": True,
            "already_started": True,
            "already_completed": True,
            "intent_id": int(intent.id),
            "payment_attempt_id": int(existing.id) if existing else None,
            "order_id": str(existing.order_id or "") if existing else None,
            "status": str(getattr(existing, "status", "completed") or "completed"),
        }
    if current_status in {"charge_started", "charge_in_progress", "awaiting_reconcile"}:
        existing = _load_payment_attempt_by_intent(int(intent.id))
        return {
            "ok": True,
            "already_started": True,
            "already_completed": False,
            "intent_id": int(intent.id),
            "payment_attempt_id": int(existing.id) if existing else None,
            "order_id": str(existing.order_id or "") if existing else None,
            "status": str(getattr(existing, "status", "charge_started") or "charge_started"),
        }
    if current_status != "ready_for_charge":
        raise BillingCheckoutValidationError("지금은 결제를 진행할 수 없는 상태예요.")

    existing_attempt = _load_payment_attempt_by_intent(int(intent.id))
    if existing_attempt:
        existing_status = str(getattr(existing_attempt, "status", "") or "").strip().lower()
        if existing_status in {"charge_started", "authorized", "reconcile_needed", "reconciled"}:
            return {
                "ok": True,
                "already_started": True,
                "already_completed": existing_status == "reconciled",
                "intent_id": int(intent.id),
                "payment_attempt_id": int(existing_attempt.id),
                "order_id": str(getattr(existing_attempt, "order_id", "") or ""),
                "status": existing_status or "charge_started",
            }

    method = resolve_checkout_billing_method(user_pk=user_id, intent=intent)
    if not method or str(method.status or "").strip().lower() != "active":
        raise BillingCheckoutValidationError("사용 가능한 결제수단이 없어요. 결제수단을 다시 등록해 주세요.")

    if not int(getattr(method, "billing_customer_id", 0) or 0):
        raise BillingCheckoutValidationError("결제수단 고객 정보를 찾지 못했어요.")
    billing_customer = BillingCustomer.query.filter_by(id=int(method.billing_customer_id), user_pk=user_id).first()
    if not billing_customer:
        raise BillingCheckoutValidationError("결제수단 고객 정보를 찾지 못했어요.")

    subscription_id = int(intent.related_subscription_id or 0)
    if intent.intent_type == INTENT_TYPE_INITIAL_SUBSCRIPTION:
        subscription_id = _ensure_initial_subscription_context(
            intent=intent,
            billing_customer_id=int(billing_customer.id),
            billing_method_id=int(method.id),
            now=now,
        )
    elif subscription_id <= 0:
        raise BillingCheckoutValidationError("구독 정보를 찾지 못해 결제를 진행할 수 없어요.")

    attempt_payload = build_payment_attempt_payload(
        user_pk=user_id,
        attempt_type=_map_intent_type_to_attempt_type(str(intent.intent_type or "")),
        amount_krw=int(intent.amount_snapshot_krw or 0),
        subscription_id=subscription_id,
        checkout_intent_id=int(intent.id),
        provider=PROVIDER_TOSS,
        currency=str(intent.currency or "KRW"),
    )
    order_id = str(attempt_payload.get("order_id") or "")
    attempt = PaymentAttempt(**attempt_payload, created_at=now, updated_at=now)
    db.session.add(attempt)

    intent.status = "charge_started"
    intent.billing_method_id = int(method.id)
    snapshot = dict(intent.pricing_snapshot_json or {})
    snapshot["payment_order_id"] = order_id
    intent.pricing_snapshot_json = snapshot
    intent.updated_at = now
    db.session.add(intent)
    if commit:
        db.session.commit()
    else:
        db.session.flush()

    from .toss_client import build_billing_key_cipher_for_version, charge_billing_key

    try:
        cipher = build_billing_key_cipher_for_version(str(method.encryption_key_version or ""))
        billing_key_plain = cipher.decrypt(str(method.billing_key_enc or ""))
    except Exception:
        attempt = PaymentAttempt.query.filter_by(id=int(attempt.id)).first()
        if attempt and str(attempt.status or "").strip().lower() == "charge_started":
            attempt.status = "failed"
            attempt.fail_code = "billing_key_decrypt_failed"
            attempt.fail_message_norm = "결제수단 보안 정보를 불러오지 못했어요."
            attempt.failed_at = datetime.now(timezone.utc)
            attempt.updated_at = datetime.now(timezone.utc)
            db.session.add(attempt)
            intent = CheckoutIntent.query.filter_by(id=int(intent.id)).first()
            if intent:
                intent.status = "failed"
                intent.completed_at = datetime.now(timezone.utc)
                intent.updated_at = datetime.now(timezone.utc)
                db.session.add(intent)
            db.session.commit()
        raise BillingCheckoutValidationError("결제수단 정보를 확인하지 못했어요. 다시 등록해 주세요.")

    response_snapshot: dict[str, Any] | None = None
    try:
        response_snapshot = charge_billing_key(
            billing_key=billing_key_plain,
            customer_key=str(billing_customer.customer_key or ""),
            amount_krw=int(intent.amount_snapshot_krw or 0),
            order_id=order_id,
            order_name="쓸수있어 구독 결제",
            idempotency_key=(str(idempotency_key or "").strip() or None),
        )
    except Exception as e:
        attempt = PaymentAttempt.query.filter_by(id=int(attempt.id)).first()
        if attempt and str(attempt.status or "").strip().lower() == "charge_started":
            attempt.status = "failed"
            attempt.fail_code = "charge_request_failed"
            attempt.fail_message_norm = normalize_fail_message(str(e))
            attempt.failed_at = datetime.now(timezone.utc)
            attempt.updated_at = datetime.now(timezone.utc)
            db.session.add(attempt)
            intent = CheckoutIntent.query.filter_by(id=int(intent.id)).first()
            if intent:
                intent.status = "failed"
                intent.completed_at = datetime.now(timezone.utc)
                intent.updated_at = datetime.now(timezone.utc)
                db.session.add(intent)
            db.session.commit()
        raise BillingCheckoutValidationError("결제 승인 요청을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.")

    from .reconcile import reconcile_by_order_id

    reconcile_result = reconcile_by_order_id(
        order_id=order_id,
        provider_snapshot=response_snapshot,
        apply_projection=True,
        commit=True,
    )
    return {
        "ok": True,
        "already_started": False,
        "intent_id": int(intent.id),
        "payment_attempt_id": int(attempt.id),
        "order_id": order_id,
        "payment_key": str((response_snapshot or {}).get("payment_key") or ""),
        "status_after": str(reconcile_result.get("status_after") or ""),
        "reconciled": bool(reconcile_result.get("reconciled")),
        "reconcile_needed": bool(reconcile_result.get("reconcile_needed")),
        "reconcile_result": reconcile_result,
    }


def resume_checkout_intent_after_registration(
    *,
    user_pk: int,
    resume_token: str,
    billing_method_id: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    user_id = int(user_pk or 0)
    token = str(resume_token or "").strip()
    if user_id <= 0 or not token:
        return {"ok": False, "resumed": False, "reason": "invalid_input"}
    intent = get_checkout_intent_by_resume_token(token, user_pk=user_id)
    if not intent:
        return {"ok": False, "resumed": False, "reason": "intent_not_found"}
    if _intent_is_expired(intent) and str(intent.status or "").strip().lower() not in {
        "completed",
        "canceled",
        "abandoned",
    }:
        intent.status = "abandoned"
        intent.completed_at = intent.completed_at or datetime.now(timezone.utc)
        intent.updated_at = datetime.now(timezone.utc)
        db.session.add(intent)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return {"ok": False, "resumed": False, "reason": "intent_expired", "intent_id": int(intent.id)}

    current = str(intent.status or "").strip().lower()
    if current in {"completed", "canceled", "abandoned"}:
        return {
            "ok": True,
            "resumed": False,
            "reason": "already_finalized_or_started",
            "intent_id": int(intent.id),
            "status": current,
        }
    if current in {"charge_started", "charge_in_progress", "awaiting_reconcile"}:
        changed = False
        if billing_method_id is not None and int(billing_method_id or 0) > 0:
            next_method_id = int(billing_method_id)
            if int(intent.billing_method_id or 0) != next_method_id:
                intent.billing_method_id = next_method_id
                changed = True
        if changed:
            intent.updated_at = datetime.now(timezone.utc)
            db.session.add(intent)
            if commit:
                db.session.commit()
            else:
                db.session.flush()
        return {
            "ok": True,
            "resumed": False,
            "reason": "charge_already_started",
            "intent_id": int(intent.id),
            "status": current,
            "resume_token": token,
        }
    if current == "ready_for_charge":
        changed = False
        if billing_method_id is not None and int(billing_method_id or 0) > 0:
            next_method_id = int(billing_method_id)
            if int(intent.billing_method_id or 0) != next_method_id:
                intent.billing_method_id = next_method_id
                changed = True
        if changed:
            intent.updated_at = datetime.now(timezone.utc)
            db.session.add(intent)
            if commit:
                db.session.commit()
            else:
                db.session.flush()
        return {
            "ok": True,
            "resumed": False,
            "reason": "already_ready_for_charge",
            "intent_id": int(intent.id),
            "status": current,
            "resume_token": token,
        }

    target = "ready_for_charge"
    next_status = transition_checkout_intent_status(current, target)
    intent.status = next_status
    if billing_method_id is not None and int(billing_method_id or 0) > 0:
        intent.billing_method_id = int(billing_method_id)
    intent.updated_at = datetime.now(timezone.utc)
    db.session.add(intent)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return {
        "ok": True,
        "resumed": True,
        "intent_id": int(intent.id),
        "status": str(intent.status or ""),
        "resume_token": token,
    }


def generate_customer_key(prefix: str = "cust") -> str:
    token = secrets.token_urlsafe(24)
    safe_prefix = str(prefix or "cust").strip().lower()
    return f"{safe_prefix}_{token}"


def generate_order_id(prefix: str = "ord") -> str:
    safe_prefix = str(prefix or "ord").strip().lower()
    return f"{safe_prefix}_{uuid.uuid4().hex}"


def normalize_fail_message(message: str | None) -> str:
    return normalize_fail_message_sec(message)


def build_registration_attempt_draft(user_pk: int, *, provider: str = PROVIDER_TOSS) -> dict[str, Any]:
    user_id = int(user_pk)
    if user_id <= 0:
        raise BillingDomainError("유효한 사용자 ID가 필요해요.")
    return {
        "user_pk": user_id,
        "provider": str(provider or PROVIDER_TOSS),
        "order_id": generate_order_id("reg"),
        "customer_key": generate_customer_key(),
        "status": "registration_started",
        "started_at": datetime.now(timezone.utc),
    }


def build_payment_attempt_draft(
    *,
    user_pk: int,
    attempt_type: str,
    amount_krw: int,
    subscription_id: int | None = None,
    provider: str = PROVIDER_TOSS,
    currency: str = "KRW",
) -> BillingOperationDraft:
    user_id = int(user_pk)
    if user_id <= 0:
        raise BillingDomainError("유효한 사용자 ID가 필요해요.")
    normalized_attempt_type = str(attempt_type or "").strip().lower()
    if normalized_attempt_type not in ATTEMPT_TYPES:
        raise BillingDomainError("지원하지 않는 결제 시도 타입이에요.")
    amount = int(amount_krw or 0)
    if amount < 0:
        raise BillingDomainError("결제 금액은 음수일 수 없어요.")
    curr = str(currency or "KRW").strip().upper()
    if curr not in SUPPORTED_CURRENCIES:
        raise BillingDomainError("지원하지 않는 통화예요.")
    _ = None if subscription_id is None else int(subscription_id)
    return BillingOperationDraft(
        user_pk=user_id,
        provider=str(provider or PROVIDER_TOSS),
        order_id=generate_order_id("pay"),
        operation_type=normalized_attempt_type,
        amount_krw=amount,
        currency=curr,
        created_at=datetime.now(timezone.utc),
    )


def build_payment_attempt_payload(
    *,
    user_pk: int,
    attempt_type: str,
    amount_krw: int,
    subscription_id: int | None = None,
    checkout_intent_id: int | None = None,
    provider: str = PROVIDER_TOSS,
    currency: str = "KRW",
) -> dict[str, Any]:
    draft = build_payment_attempt_draft(
        user_pk=user_pk,
        attempt_type=attempt_type,
        amount_krw=amount_krw,
        subscription_id=subscription_id,
        provider=provider,
        currency=currency,
    )
    return {
        "user_pk": draft.user_pk,
        "provider": draft.provider,
        "subscription_id": subscription_id,
        "checkout_intent_id": (None if checkout_intent_id is None else int(checkout_intent_id)),
        "attempt_type": draft.operation_type,
        "order_id": draft.order_id,
        "amount_krw": draft.amount_krw,
        "currency": draft.currency,
        "status": "charge_started",
        "requested_at": draft.created_at,
    }


def derive_subscription_preview(subscription: Any, items: list[Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "next_billing_amount_krw": derive_next_billing_amount(subscription, items, at=now),
        "in_grace": is_subscription_in_grace(subscription, now=now),
        "should_past_due": should_transition_to_past_due(subscription, now=now),
    }


def build_event_dedupe_token(
    *,
    order_id: str | None = None,
    payment_key: str | None = None,
    transmission_id: str | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    return build_idempotency_token(
        order_id=order_id,
        payment_key=payment_key,
        transmission_id=transmission_id,
        event_payload=event_payload,
    )


def apply_subscription_transition(current_status: str, event: str) -> str:
    return transition_subscription_state(current_status, event)


def apply_payment_attempt_transition(current_status: str, event: str) -> str:
    return transition_payment_attempt_state(current_status, event)


def get_or_create_billing_customer(
    *,
    user_pk: int,
    provider: str = PROVIDER_TOSS,
) -> BillingCustomer:
    user_id = int(user_pk)
    if user_id <= 0:
        raise BillingRegistrationError("유효한 사용자 ID가 필요해요.")
    provider_name = str(provider or PROVIDER_TOSS).strip().lower() or PROVIDER_TOSS
    row = BillingCustomer.query.filter_by(user_pk=user_id, provider=provider_name).first()
    if row:
        return row

    row = BillingCustomer(
        user_pk=user_id,
        provider=provider_name,
        customer_key=generate_customer_key("cust"),
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.session.add(row)
    try:
        db.session.flush()
        return row
    except IntegrityError:
        # 동시 등록 시작 경합 시(unique user/provider) 이미 생성된 row를 재사용한다.
        db.session.rollback()
        existing = BillingCustomer.query.filter_by(user_pk=user_id, provider=provider_name).first()
        if existing:
            return existing
        raise


def start_registration_attempt(
    *,
    user_pk: int,
    provider: str = PROVIDER_TOSS,
) -> BillingMethodRegistrationAttempt:
    customer = get_or_create_billing_customer(user_pk=int(user_pk), provider=provider)
    draft = build_registration_attempt_draft(user_pk=int(user_pk), provider=str(provider or PROVIDER_TOSS))
    attempt = BillingMethodRegistrationAttempt(
        user_pk=int(user_pk),
        billing_customer_id=int(customer.id),
        provider=str(draft["provider"]),
        order_id=str(draft["order_id"]),
        customer_key=str(customer.customer_key),
        status=str(draft["status"]),
        started_at=draft["started_at"],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.session.add(attempt)
    db.session.commit()
    return attempt


def _lock_registration_attempt_by_order(order_id: str) -> BillingMethodRegistrationAttempt | None:
    oid = str(order_id or "").strip()
    if not oid:
        return None
    query = BillingMethodRegistrationAttempt.query.filter_by(order_id=oid)
    try:
        return query.with_for_update().first()
    except Exception:
        # sqlite/테스트 환경에서는 FOR UPDATE가 무시되거나 실패할 수 있어 fallback 한다.
        return query.first()


def mark_registration_failed(
    *,
    user_pk: int,
    order_id: str,
    fail_code: str | None = None,
    fail_message: str | None = None,
) -> BillingMethodRegistrationAttempt | None:
    user_id = int(user_pk)
    oid = str(order_id or "").strip()
    if user_id <= 0 or not oid:
        return None
    attempt = _lock_registration_attempt_by_order(oid)
    if attempt and int(attempt.user_pk or 0) != user_id:
        attempt = None
    if not attempt:
        return None
    if str(attempt.status or "") == "billing_key_issued":
        return attempt
    attempt.status = "failed"
    attempt.fail_code = (str(fail_code or "").strip() or None)
    attempt.fail_message_norm = normalize_fail_message(fail_message)
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.updated_at = datetime.now(timezone.utc)
    db.session.add(attempt)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return BillingMethodRegistrationAttempt.query.filter_by(user_pk=user_id, order_id=oid).first()
    return attempt


def mark_registration_failed_by_order(
    *,
    order_id: str,
    fail_code: str | None = None,
    fail_message: str | None = None,
) -> BillingMethodRegistrationAttempt | None:
    oid = str(order_id or "").strip()
    if not oid:
        return None
    attempt = _lock_registration_attempt_by_order(oid)
    if not attempt:
        return None
    if str(attempt.status or "") == "billing_key_issued":
        return attempt
    attempt.status = "failed"
    attempt.fail_code = (str(fail_code or "").strip() or None)
    attempt.fail_message_norm = normalize_fail_message(fail_message)
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.updated_at = datetime.now(timezone.utc)
    db.session.add(attempt)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return BillingMethodRegistrationAttempt.query.filter_by(order_id=oid).first()
    return attempt


def _complete_registration_success_for_attempt(
    *,
    attempt: BillingMethodRegistrationAttempt,
    auth_key: str,
    customer_key: str | None,
    exchange_auth_key_fn,
    key_cipher: BillingKeyCipher,
    encryption_key_version: str | None = None,
) -> dict[str, Any]:
    user_id = int(attempt.user_pk or 0)
    if user_id <= 0:
        raise BillingRegistrationError("등록 사용자 정보를 확인할 수 없어요.")
    oid = str(attempt.order_id or "").strip()
    raw_auth_key = str(auth_key or "").strip()
    provided_customer_key = str(customer_key or "").strip()
    if not oid or not raw_auth_key:
        raise BillingRegistrationError("등록 완료 확인에 필요한 정보가 부족해요.")

    if provided_customer_key and provided_customer_key != str(attempt.customer_key or ""):
        raise BillingRegistrationError("등록 고객키가 일치하지 않아요.")

    if str(attempt.status or "") == "billing_key_issued":
        existing = (
            BillingMethod.query.filter_by(
                user_pk=user_id,
                provider=str(attempt.provider or PROVIDER_TOSS),
                status="active",
            )
            .order_by(BillingMethod.id.desc())
            .first()
        )
        return {
            "ok": True,
            "already_completed": True,
            "billing_method_id": int(existing.id) if existing else None,
            "attempt_id": int(attempt.id),
            "user_pk": int(user_id),
            "order_id": oid,
        }

    try:
        issue_result = exchange_auth_key_fn(
            auth_key=raw_auth_key,
            customer_key=str(attempt.customer_key or ""),
        )
    except Exception as e:
        mark_registration_failed_by_order(
            order_id=oid,
            fail_code="issue_failed",
            fail_message=str(e),
        )
        raise BillingRegistrationError("결제수단 등록을 완료하지 못했어요.") from e

    issued_billing_key = str((issue_result or {}).get("billing_key") or "").strip()
    if not issued_billing_key:
        mark_registration_failed_by_order(
            order_id=oid,
            fail_code="billing_key_missing",
            fail_message="토스 응답에서 billing_key를 찾지 못했어요.",
        )
        raise BillingRegistrationError("결제수단 등록 정보가 올바르지 않아요.")

    key_hash = hash_billing_key(issued_billing_key)
    key_enc = encrypt_billing_key(issued_billing_key, cipher=key_cipher)

    provider = str(attempt.provider or PROVIDER_TOSS)
    customer = get_or_create_billing_customer(user_pk=user_id, provider=provider)
    method = BillingMethod.query.filter_by(provider=provider, billing_key_hash=key_hash).first()
    if method and int(method.user_pk) != user_id:
        mark_registration_failed_by_order(
            order_id=oid,
            fail_code="billing_key_conflict",
            fail_message="이미 다른 계정에서 사용 중인 결제수단이에요.",
        )
        raise BillingRegistrationError("다른 계정에서 사용 중인 결제수단이에요.")

    now = datetime.now(timezone.utc)
    if not method:
        method = BillingMethod(
            user_pk=user_id,
            billing_customer_id=int(customer.id),
            provider=provider,
            method_type="card",
            billing_key_enc=key_enc,
            billing_key_hash=key_hash,
            encryption_key_version=(str(encryption_key_version or "").strip() or None),
            status="active",
            issued_at=now,
            created_at=now,
            updated_at=now,
        )
    else:
        method.user_pk = user_id
        method.billing_customer_id = int(customer.id)
        method.billing_key_enc = key_enc
        method.encryption_key_version = (str(encryption_key_version or "").strip() or method.encryption_key_version)
        method.status = "active"
        if not method.issued_at:
            method.issued_at = now
        method.updated_at = now
    db.session.add(method)
    # 신규 method는 id가 확정돼야 self-deactivate를 피할 수 있다.
    db.session.flush()
    method_id = int(method.id or 0)

    # 같은 사용자의 기존 active 수단은 inactive로 정리(가장 최근 등록 건 1개만 active)
    prev_methods = (
        BillingMethod.query.filter(BillingMethod.user_pk == user_id)
        .filter(BillingMethod.provider == provider)
        .filter(BillingMethod.id != method_id)
        .filter(BillingMethod.status == "active")
        .all()
    )
    for row in prev_methods:
        row.status = "inactive"
        row.updated_at = now
        db.session.add(row)

    attempt.status = "billing_key_issued"
    attempt.fail_code = None
    attempt.fail_message_norm = None
    attempt.completed_at = now
    attempt.updated_at = now
    db.session.add(attempt)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing_attempt = BillingMethodRegistrationAttempt.query.filter_by(order_id=oid).first()
        if existing_attempt and str(existing_attempt.status or "") == "billing_key_issued":
            existing_method = (
                BillingMethod.query.filter_by(
                    user_pk=user_id,
                    provider=provider,
                    status="active",
                )
                .order_by(BillingMethod.id.desc())
                .first()
            )
            return {
                "ok": True,
                "already_completed": True,
                "billing_method_id": int(existing_method.id) if existing_method else None,
                "attempt_id": int(existing_attempt.id),
                "user_pk": int(user_id),
                "order_id": oid,
            }
        raise BillingRegistrationError("등록 완료 처리 중 중복 요청이 감지되었어요. 잠시 후 다시 확인해 주세요.")

    return {
        "ok": True,
        "already_completed": False,
        "billing_method_id": int(method.id),
        "attempt_id": int(attempt.id),
        "user_pk": int(user_id),
        "order_id": oid,
    }


def complete_registration_success(
    *,
    user_pk: int,
    order_id: str,
    auth_key: str,
    customer_key: str | None,
    exchange_auth_key_fn,
    key_cipher: BillingKeyCipher,
    encryption_key_version: str | None = None,
) -> dict[str, Any]:
    user_id = int(user_pk)
    oid = str(order_id or "").strip()
    raw_auth_key = str(auth_key or "").strip()
    provided_customer_key = str(customer_key or "").strip()
    if user_id <= 0 or not oid or not raw_auth_key:
        raise BillingRegistrationError("등록 완료 확인에 필요한 정보가 부족해요.")

    attempt = _lock_registration_attempt_by_order(oid)
    if attempt and int(attempt.user_pk or 0) != user_id:
        attempt = None
    if not attempt:
        raise BillingRegistrationError("등록 시도 정보를 찾지 못했어요.")
    return _complete_registration_success_for_attempt(
        attempt=attempt,
        auth_key=raw_auth_key,
        customer_key=provided_customer_key,
        exchange_auth_key_fn=exchange_auth_key_fn,
        key_cipher=key_cipher,
        encryption_key_version=encryption_key_version,
    )


def complete_registration_success_by_order(
    *,
    order_id: str,
    auth_key: str,
    customer_key: str | None,
    exchange_auth_key_fn,
    key_cipher: BillingKeyCipher,
    encryption_key_version: str | None = None,
) -> dict[str, Any]:
    oid = str(order_id or "").strip()
    raw_auth_key = str(auth_key or "").strip()
    provided_customer_key = str(customer_key or "").strip()
    if not oid or not raw_auth_key:
        raise BillingRegistrationError("등록 완료 확인에 필요한 정보가 부족해요.")
    attempt = _lock_registration_attempt_by_order(oid)
    if not attempt:
        raise BillingRegistrationError("등록 시도 정보를 찾지 못했어요.")
    return _complete_registration_success_for_attempt(
        attempt=attempt,
        auth_key=raw_auth_key,
        customer_key=provided_customer_key,
        exchange_auth_key_fn=exchange_auth_key_fn,
        key_cipher=key_cipher,
        encryption_key_version=encryption_key_version,
    )


def _sanitize_event_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    # 원문 전체 저장 대신, 운영/복구에 필요한 최소 필드만 남긴다.
    out: dict[str, Any] = {}
    event_type = str(
        data.get("eventType")
        or data.get("event_type")
        or data.get("type")
        or "unknown"
    ).strip()[:64]
    status = str(data.get("status") or "received").strip()[:32]
    order_id = normalize_order_id(
        str(data.get("orderId") or data.get("order_id") or data.get("orderID") or "")
    )
    payment_key = normalize_payment_key(
        str(data.get("paymentKey") or data.get("payment_key") or "")
    )
    if event_type:
        out["event_type"] = event_type
    if status:
        out["status"] = status
    if order_id:
        out["order_id"] = order_id
    if payment_key:
        out["payment_key"] = payment_key
    if "totalAmount" in data:
        try:
            out["total_amount"] = int(data.get("totalAmount") or 0)
        except Exception:
            pass
    if "currency" in data:
        out["currency"] = str(data.get("currency") or "").strip()[:8]
    if "code" in data:
        out["code"] = normalize_fail_message(str(data.get("code") or ""))[:64]
    if "message" in data:
        out["message"] = normalize_fail_message(str(data.get("message") or ""))[:255]
    for key in ("approvedAt", "requestedAt", "method"):
        if key in data:
            out[key] = normalize_fail_message(str(data.get(key) or ""))[:80]
    return out


def ingest_payment_event(
    *,
    payload: Mapping[str, Any] | None,
    provider: str = PROVIDER_TOSS,
    transmission_id: str | None = None,
    user_pk: int | None = None,
) -> dict[str, Any]:
    safe_payload = _sanitize_event_payload(payload)
    provider_name = str(provider or PROVIDER_TOSS).strip().lower() or PROVIDER_TOSS
    tx_id = normalize_transmission_id(
        transmission_id
        or str(
            (payload or {}).get("transmissionId")
            or (payload or {}).get("eventId")
            or ""
        )
    )
    related_order_id = normalize_order_id(str(safe_payload.get("order_id") or ""))
    related_payment_key = normalize_payment_key(str(safe_payload.get("payment_key") or ""))
    event_hash = build_event_hash(safe_payload)
    now = datetime.now(timezone.utc)

    existing = None
    if tx_id:
        existing = PaymentEvent.query.filter_by(provider=provider_name, transmission_id=tx_id).first()
    if not existing:
        existing = PaymentEvent.query.filter_by(provider=provider_name, event_hash=event_hash).first()
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "status": "ignored_duplicate",
            "payment_event_id": int(existing.id),
            "needs_reconcile": False,
        }

    row = PaymentEvent(
        user_pk=(int(user_pk) if user_pk and int(user_pk) > 0 else None),
        provider=provider_name,
        event_type=str(safe_payload.get("event_type") or "unknown")[:64],
        status="received",
        transmission_id=tx_id,
        event_hash=event_hash,
        related_order_id=related_order_id,
        related_payment_key=related_payment_key,
        payload_json=safe_payload,
        received_at=now,
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        dup = None
        if tx_id:
            dup = PaymentEvent.query.filter_by(provider=provider_name, transmission_id=tx_id).first()
        if not dup:
            dup = PaymentEvent.query.filter_by(provider=provider_name, event_hash=event_hash).first()
        if dup:
            return {
                "ok": True,
                "duplicate": True,
                "status": "ignored_duplicate",
                "payment_event_id": int(dup.id),
                "needs_reconcile": False,
            }
        raise
    return {
        "ok": True,
        "duplicate": False,
        "status": "received",
        "payment_event_id": int(row.id),
        "needs_reconcile": True,
    }


def normalize_registration_attempts_abandoned(
    *,
    abandoned_after_hours: int = 2,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=max(1, int(abandoned_after_hours or 2)))
    rows = (
        BillingMethodRegistrationAttempt.query.filter_by(status="registration_started")
        .filter(BillingMethodRegistrationAttempt.started_at <= cutoff)
        .all()
    )
    if not rows:
        return 0
    touched = 0
    for row in rows:
        row.status = "canceled"
        row.fail_code = row.fail_code or "abandoned"
        row.fail_message_norm = row.fail_message_norm or "등록 확인이 완료되지 않아 자동 종료되었어요."
        row.completed_at = row.completed_at or datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        db.session.add(row)
        touched += 1
    if touched > 0:
        db.session.commit()
    return touched


def cleanup_registration_attempts(
    *,
    retention_days: int = 90,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    base_now = now or datetime.now(timezone.utc)
    cutoff = base_now - timedelta(days=max(1, int(retention_days or 90)))
    query = BillingMethodRegistrationAttempt.query.filter(
        BillingMethodRegistrationAttempt.status.in_(("failed", "canceled"))
    ).filter(BillingMethodRegistrationAttempt.updated_at <= cutoff)
    rows = query.all()
    purged = int(len(rows))
    if (not dry_run) and purged > 0:
        for row in rows:
            db.session.delete(row)
        db.session.commit()
    return {
        "purged_count": purged,
        "retention_days": max(1, int(retention_days or 90)),
        "dry_run": 1 if dry_run else 0,
    }


def reconcile_payment_by_order_id(
    *,
    order_id: str,
    apply_projection: bool = True,
) -> dict[str, Any]:
    from .reconcile import reconcile_by_order_id

    return reconcile_by_order_id(
        order_id=order_id,
        apply_projection=bool(apply_projection),
        commit=True,
    )


def reconcile_payment_by_payment_key(
    *,
    payment_key: str,
    apply_projection: bool = True,
) -> dict[str, Any]:
    from .reconcile import reconcile_by_payment_key

    return reconcile_by_payment_key(
        payment_key=payment_key,
        apply_projection=bool(apply_projection),
        commit=True,
    )


def reconcile_payment_from_event(
    *,
    payment_event_id: int | None = None,
    transmission_id: str | None = None,
    apply_projection: bool = True,
) -> dict[str, Any]:
    from .reconcile import reconcile_from_payment_event

    return reconcile_from_payment_event(
        payment_event_id=payment_event_id,
        transmission_id=transmission_id,
        apply_projection=bool(apply_projection),
        commit=True,
    )
