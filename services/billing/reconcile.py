from __future__ import annotations

from dataclasses import dataclass
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import or_

from core.extensions import db
from domain.models import PaymentAttempt, PaymentEvent, Subscription

from .constants import (
    GRACE_DAYS,
    PAYMENT_STATUS_AUTHORIZED,
    PAYMENT_STATUS_CANCELED,
    PAYMENT_STATUS_CHARGE_STARTED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_RECONCILED,
    PAYMENT_STATUS_RECONCILE_NEEDED,
    SUB_STATUS_ACTIVE,
    SUB_STATUS_CANCEL_REQUESTED,
    SUB_STATUS_GRACE_STARTED,
    SUB_STATUS_PAST_DUE,
    SUB_STATUS_PENDING_ACTIVATION,
)
from .idempotency import normalize_order_id, normalize_payment_key, normalize_transmission_id
from .pricing import should_transition_to_past_due
from .state_machine import (
    EVENT_PAYMARK_AUTHORIZED,
    EVENT_PAYMARK_CANCELED,
    EVENT_PAYMARK_FAILED,
    EVENT_PAYMARK_RECONCILED,
    EVENT_PAYMARK_RECONCILE_NEEDED,
    EVENT_SUB_ACTIVATE,
    EVENT_SUB_CANCEL_EFFECTIVE,
    EVENT_SUB_MARK_PAST_DUE,
    EVENT_SUB_RECOVER_PAYMENT,
    EVENT_SUB_START_GRACE,
    StateTransitionError,
    transition_payment_attempt_state,
    transition_subscription_state,
)
from .toss_client import TossBillingApiError, fetch_payment_snapshot


TERMINAL_ATTEMPT_STATUSES = {
    PAYMENT_STATUS_RECONCILED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_CANCELED,
}
SUCCESS_PROVIDER_STATUSES = {"done", "paid", "approved", "success", "successful"}
FAILED_PROVIDER_STATUSES = {
    "failed",
    "aborted",
    "canceled",
    "cancelled",
    "expired",
    "partial_canceled",
    "rejected",
}


class BillingReconcileError(RuntimeError):
    pass


class BillingReconcileNotFound(BillingReconcileError):
    pass


@dataclass(frozen=True)
class ReconcileSnapshot:
    provider_status: str | None
    order_id: str | None
    payment_key: str | None
    amount_krw: int | None
    currency: str | None
    fail_code: str | None
    fail_message: str | None
    approved_at: datetime | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _normalize_provider_status(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return raw or None


def _snapshot_from_mapping(payload: Mapping[str, Any] | None) -> ReconcileSnapshot | None:
    if not payload:
        return None
    provider_status = _normalize_provider_status(payload.get("provider_status") or payload.get("status"))
    return ReconcileSnapshot(
        provider_status=provider_status,
        order_id=normalize_order_id(payload.get("order_id") if isinstance(payload.get("order_id"), str) else str(payload.get("order_id") or "")),
        payment_key=normalize_payment_key(payload.get("payment_key") if isinstance(payload.get("payment_key"), str) else str(payload.get("payment_key") or "")),
        amount_krw=_to_int(payload.get("total_amount") or payload.get("amount_krw") or payload.get("amount")),
        currency=str(payload.get("currency") or "").strip().upper() or None,
        fail_code=str(payload.get("fail_code") or payload.get("code") or "").strip() or None,
        fail_message=str(payload.get("fail_message") or payload.get("message") or "").strip() or None,
        approved_at=_parse_dt(payload.get("approved_at") or payload.get("approvedAt")),
    )


def _latest_event_snapshot_for_attempt(attempt: PaymentAttempt) -> ReconcileSnapshot | None:
    provider = str(attempt.provider or "toss")
    query = PaymentEvent.query.filter(PaymentEvent.provider == provider)
    payment_key = normalize_payment_key(str(attempt.payment_key or ""))
    order_id = normalize_order_id(str(attempt.order_id or ""))
    if payment_key and order_id:
        query = query.filter(
            or_(
                PaymentEvent.related_payment_key == payment_key,
                PaymentEvent.related_order_id == order_id,
            )
        )
    elif payment_key:
        query = query.filter(PaymentEvent.related_payment_key == payment_key)
    elif order_id:
        query = query.filter(PaymentEvent.related_order_id == order_id)
    else:
        return None

    row = query.order_by(PaymentEvent.received_at.desc(), PaymentEvent.id.desc()).first()
    if not row:
        return None
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    merged = {
        "status": payload.get("status"),
        "order_id": payload.get("order_id") or row.related_order_id,
        "payment_key": payload.get("payment_key") or row.related_payment_key,
        "total_amount": payload.get("total_amount"),
        "currency": payload.get("currency"),
        "code": payload.get("code"),
        "message": payload.get("message"),
        "approvedAt": payload.get("approvedAt"),
    }
    return _snapshot_from_mapping(merged)


def _fetch_provider_snapshot_for_attempt(
    attempt: PaymentAttempt,
    *,
    provider_lookup_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> ReconcileSnapshot | None:
    lookup = provider_lookup_fn or fetch_payment_snapshot
    payment_key = normalize_payment_key(str(attempt.payment_key or ""))
    order_id = normalize_order_id(str(attempt.order_id or ""))
    if not payment_key and not order_id:
        return None
    try:
        payload = lookup(order_id=order_id, payment_key=payment_key)
    except TossBillingApiError:
        return None
    except Exception:
        return None
    return _snapshot_from_mapping(payload)


def _lock_payment_attempt_by_order(order_id: str) -> PaymentAttempt | None:
    oid = normalize_order_id(order_id)
    if not oid:
        return None
    query = PaymentAttempt.query.filter_by(order_id=oid)
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _lock_payment_attempt_by_payment_key(payment_key: str) -> PaymentAttempt | None:
    pkey = normalize_payment_key(payment_key)
    if not pkey:
        return None
    query = PaymentAttempt.query.filter_by(payment_key=pkey)
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _lock_payment_event(*, payment_event_id: int | None = None, transmission_id: str | None = None) -> PaymentEvent | None:
    query = PaymentEvent.query
    if payment_event_id:
        query = query.filter_by(id=int(payment_event_id))
    elif transmission_id:
        tx = normalize_transmission_id(transmission_id)
        if not tx:
            return None
        query = query.filter_by(transmission_id=tx)
    else:
        return None
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _lock_subscription(subscription_id: int | None) -> Subscription | None:
    if not subscription_id:
        return None
    query = Subscription.query.filter_by(id=int(subscription_id))
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _transition_payment_status(current: str, event: str) -> str:
    try:
        return transition_payment_attempt_state(current, event)
    except StateTransitionError:
        return str(current or "").strip().lower()


def _mark_reconcile_needed(
    attempt: PaymentAttempt,
    *,
    reason_code: str,
    reason_message: str | None,
    now: datetime,
    force: bool = False,
) -> None:
    current = str(attempt.status or "").strip().lower()
    if force:
        attempt.status = PAYMENT_STATUS_RECONCILE_NEEDED
    elif current == PAYMENT_STATUS_CHARGE_STARTED:
        attempt.status = PAYMENT_STATUS_RECONCILE_NEEDED
    elif current == PAYMENT_STATUS_AUTHORIZED:
        attempt.status = _transition_payment_status(current, EVENT_PAYMARK_RECONCILE_NEEDED)
    elif current in {PAYMENT_STATUS_RECONCILE_NEEDED, PAYMENT_STATUS_FAILED, PAYMENT_STATUS_RECONCILED, PAYMENT_STATUS_CANCELED}:
        pass
    else:
        attempt.status = PAYMENT_STATUS_RECONCILE_NEEDED
    attempt.fail_code = reason_code[:64]
    attempt.fail_message_norm = str(reason_message or "결제 상태를 확정하지 못했어요.")[:255]
    attempt.updated_at = now


def _mark_success(attempt: PaymentAttempt, *, snapshot: ReconcileSnapshot, now: datetime) -> bool:
    changed = False
    current = str(attempt.status or "").strip().lower()
    if current == PAYMENT_STATUS_CHARGE_STARTED:
        next_status = _transition_payment_status(current, EVENT_PAYMARK_AUTHORIZED)
        if next_status != current:
            attempt.status = next_status
            changed = True
            current = next_status
    if current == PAYMENT_STATUS_AUTHORIZED:
        next_status = _transition_payment_status(current, EVENT_PAYMARK_RECONCILED)
        if next_status != current:
            attempt.status = next_status
            changed = True
            current = next_status
    elif current == PAYMENT_STATUS_RECONCILE_NEEDED:
        next_status = _transition_payment_status(current, EVENT_PAYMARK_RECONCILED)
        if next_status != current:
            attempt.status = next_status
            changed = True
            current = next_status
    elif current in TERMINAL_ATTEMPT_STATUSES:
        return False

    if snapshot.payment_key and snapshot.payment_key != str(attempt.payment_key or ""):
        attempt.payment_key = snapshot.payment_key
        changed = True
    if not attempt.authorized_at:
        attempt.authorized_at = snapshot.approved_at or now
        changed = True
    if current == PAYMENT_STATUS_RECONCILED and not attempt.reconciled_at:
        attempt.reconciled_at = now
        changed = True
    if attempt.fail_code is not None:
        attempt.fail_code = None
        changed = True
    if attempt.fail_message_norm is not None:
        attempt.fail_message_norm = None
        changed = True
    if changed:
        attempt.updated_at = now
    return changed


def _mark_failed(attempt: PaymentAttempt, *, snapshot: ReconcileSnapshot, now: datetime) -> bool:
    changed = False
    current = str(attempt.status or "").strip().lower()
    canceled = bool(snapshot.provider_status in {"canceled", "cancelled", "partial_canceled"})
    if current == PAYMENT_STATUS_CHARGE_STARTED:
        event = EVENT_PAYMARK_CANCELED if canceled else EVENT_PAYMARK_FAILED
        next_status = _transition_payment_status(current, event)
        if next_status != current:
            attempt.status = next_status
            changed = True
    elif current in {PAYMENT_STATUS_AUTHORIZED, PAYMENT_STATUS_RECONCILE_NEEDED}:
        next_status = _transition_payment_status(current, EVENT_PAYMARK_FAILED)
        if next_status != current:
            attempt.status = next_status
            changed = True
    elif current in TERMINAL_ATTEMPT_STATUSES:
        return False
    else:
        attempt.status = PAYMENT_STATUS_FAILED
        changed = True

    if snapshot.payment_key and snapshot.payment_key != str(attempt.payment_key or ""):
        attempt.payment_key = snapshot.payment_key
        changed = True
    fail_code = str(snapshot.fail_code or "payment_failed")[:64]
    fail_message = str(snapshot.fail_message or "결제 승인에 실패했어요.")[:255]
    if attempt.fail_code != fail_code:
        attempt.fail_code = fail_code
        changed = True
    if attempt.fail_message_norm != fail_message:
        attempt.fail_message_norm = fail_message
        changed = True
    if not attempt.failed_at:
        attempt.failed_at = now
        changed = True
    if changed:
        attempt.updated_at = now
    return changed


def _safe_transition_subscription(subscription: Subscription, event: str) -> None:
    current = str(subscription.status or "").strip().lower()
    try:
        subscription.status = transition_subscription_state(current, event)
    except StateTransitionError:
        return


def _add_month(dt: datetime) -> datetime:
    year = int(dt.year)
    month = int(dt.month) + 1
    if month > 12:
        year += 1
        month = 1
    day = min(int(dt.day), int(monthrange(year, month)[1]))
    return dt.replace(year=year, month=month, day=day)


def _advance_subscription_cycle(subscription: Subscription, *, now: datetime) -> None:
    current_start = _parse_dt(subscription.current_period_start) or _parse_dt(subscription.billing_anchor_at) or now
    current_end = _parse_dt(subscription.current_period_end) or _parse_dt(subscription.next_billing_at)
    if current_end is None or current_end <= current_start:
        current_end = _add_month(current_start)

    next_start = current_end
    next_end = _add_month(next_start)

    subscription.billing_anchor_at = subscription.billing_anchor_at or current_start
    subscription.current_period_start = next_start
    subscription.current_period_end = next_end
    subscription.next_billing_at = next_end


def _apply_subscription_policy(
    *,
    subscription: Subscription | None,
    attempt: PaymentAttempt,
    now: datetime,
    attempt_before: str,
    attempt_after: str,
) -> tuple[str | None, str | None]:
    if not subscription:
        return None, None
    before = str(subscription.status or "").strip().lower()
    attempt_type = str(getattr(attempt, "attempt_type", "") or "").strip().lower()
    is_recurring_family = attempt_type in {"recurring", "retry"}

    if before == SUB_STATUS_CANCEL_REQUESTED and subscription.cancel_effective_at:
        try:
            cancel_effective_at = _parse_dt(subscription.cancel_effective_at)
            if cancel_effective_at and now >= cancel_effective_at:
                _safe_transition_subscription(subscription, EVENT_SUB_CANCEL_EFFECTIVE)
                subscription.canceled_at = subscription.canceled_at or now
        except Exception:
            pass

    if attempt_after == PAYMENT_STATUS_RECONCILED and attempt_before != attempt_after:
        if before == SUB_STATUS_PENDING_ACTIVATION:
            _safe_transition_subscription(subscription, EVENT_SUB_ACTIVATE)
        elif before in {SUB_STATUS_GRACE_STARTED, SUB_STATUS_PAST_DUE}:
            _safe_transition_subscription(subscription, EVENT_SUB_RECOVER_PAYMENT)
        if is_recurring_family:
            _advance_subscription_cycle(subscription, now=now)
        subscription.last_paid_at = now
        subscription.grace_until = None
        subscription.last_failed_at = None
        subscription.retry_count = 0
        subscription.updated_at = now
    elif (
        attempt_after in {PAYMENT_STATUS_FAILED, PAYMENT_STATUS_CANCELED}
        and attempt_before != attempt_after
        and is_recurring_family
    ):
        if before == SUB_STATUS_ACTIVE:
            _safe_transition_subscription(subscription, EVENT_SUB_START_GRACE)
            subscription.grace_until = now + timedelta(days=GRACE_DAYS)
        elif before == SUB_STATUS_GRACE_STARTED and should_transition_to_past_due(subscription, now=now):
            _safe_transition_subscription(subscription, EVENT_SUB_MARK_PAST_DUE)
        subscription.last_failed_at = now
        subscription.retry_count = max(0, int(subscription.retry_count or 0)) + 1
        subscription.updated_at = now
    elif before == SUB_STATUS_GRACE_STARTED and should_transition_to_past_due(subscription, now=now):
        _safe_transition_subscription(subscription, EVENT_SUB_MARK_PAST_DUE)
        subscription.updated_at = now

    after = str(subscription.status or "").strip().lower()
    return before, after


def reconcile_attempt_with_snapshot(
    *,
    attempt: PaymentAttempt,
    snapshot: ReconcileSnapshot | None,
    subscription: Subscription | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_now = now or _now()
    attempt_before = str(attempt.status or "").strip().lower()
    subscription_before: str | None = str(subscription.status or "").strip().lower() if subscription else None

    if attempt_before in TERMINAL_ATTEMPT_STATUSES:
        sub_before, sub_after = _apply_subscription_policy(
            subscription=subscription,
            attempt=attempt,
            now=current_now,
            attempt_before=attempt_before,
            attempt_after=attempt_before,
        )
        return {
            "payment_attempt_id": int(attempt.id),
            "status_before": attempt_before,
            "status_after": attempt_before,
            "subscription_status_before": sub_before or subscription_before,
            "subscription_status_after": sub_after or subscription_before,
            "reconciled": attempt_before == PAYMENT_STATUS_RECONCILED,
            "finalized": True,
            "reason": "already_finalized",
        }

    if snapshot is None:
        _mark_reconcile_needed(
            attempt,
            reason_code="snapshot_missing",
            reason_message="결제 상태 조회 응답이 없어 재확인이 필요해요.",
            now=current_now,
        )
    else:
        status = snapshot.provider_status
        amount_mismatch = snapshot.amount_krw is not None and int(snapshot.amount_krw) != int(attempt.amount_krw or 0)
        currency_mismatch = bool(snapshot.currency) and str(snapshot.currency).upper() != str(attempt.currency or "KRW").upper()

        if status in SUCCESS_PROVIDER_STATUSES:
            if amount_mismatch or currency_mismatch:
                _mark_reconcile_needed(
                    attempt,
                    reason_code="amount_or_currency_mismatch",
                    reason_message="결제 금액 또는 통화가 내부 값과 달라 재확인이 필요해요.",
                    now=current_now,
                )
            else:
                _mark_success(attempt, snapshot=snapshot, now=current_now)
        elif status in FAILED_PROVIDER_STATUSES:
            _mark_failed(attempt, snapshot=snapshot, now=current_now)
        else:
            _mark_reconcile_needed(
                attempt,
                reason_code="provider_status_unknown",
                reason_message="PG 결제 상태를 확정할 수 없어 재확인이 필요해요.",
                now=current_now,
            )

    attempt_after = str(attempt.status or "").strip().lower()
    sub_before, sub_after = _apply_subscription_policy(
        subscription=subscription,
        attempt=attempt,
        now=current_now,
        attempt_before=attempt_before,
        attempt_after=attempt_after,
    )

    return {
        "payment_attempt_id": int(attempt.id),
        "status_before": attempt_before,
        "status_after": attempt_after,
        "subscription_status_before": sub_before or subscription_before,
        "subscription_status_after": sub_after if subscription else None,
        "reconciled": attempt_after == PAYMENT_STATUS_RECONCILED,
        "finalized": attempt_after in TERMINAL_ATTEMPT_STATUSES,
        "reconcile_needed": attempt_after == PAYMENT_STATUS_RECONCILE_NEEDED,
        "reason": "reconciled" if attempt_after == PAYMENT_STATUS_RECONCILED else attempt_after,
    }


def reconcile_by_order_id(
    *,
    order_id: str,
    provider_lookup_fn: Callable[..., Mapping[str, Any]] | None = None,
    provider_snapshot: Mapping[str, Any] | None = None,
    apply_projection: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    attempt = _lock_payment_attempt_by_order(order_id)
    if not attempt:
        raise BillingReconcileNotFound("order_id에 해당하는 결제 시도를 찾지 못했어요.")
    subscription = _lock_subscription(int(attempt.subscription_id or 0))
    snapshot = _snapshot_from_mapping(provider_snapshot) if provider_snapshot else None
    if snapshot is None:
        snapshot = _fetch_provider_snapshot_for_attempt(attempt, provider_lookup_fn=provider_lookup_fn)
    if snapshot is None:
        snapshot = _latest_event_snapshot_for_attempt(attempt)
    result = reconcile_attempt_with_snapshot(
        attempt=attempt,
        snapshot=snapshot,
        subscription=subscription,
    )
    db.session.add(attempt)
    if subscription:
        db.session.add(subscription)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    if apply_projection:
        _apply_projection_after_reconcile(
            attempt_id=int(attempt.id),
            order_id=str(attempt.order_id or ""),
            result=result,
        )
    return result


def reconcile_by_payment_key(
    *,
    payment_key: str,
    provider_lookup_fn: Callable[..., Mapping[str, Any]] | None = None,
    provider_snapshot: Mapping[str, Any] | None = None,
    apply_projection: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    attempt = _lock_payment_attempt_by_payment_key(payment_key)
    if not attempt:
        raise BillingReconcileNotFound("payment_key에 해당하는 결제 시도를 찾지 못했어요.")
    subscription = _lock_subscription(int(attempt.subscription_id or 0))
    snapshot = _snapshot_from_mapping(provider_snapshot) if provider_snapshot else None
    if snapshot is None:
        snapshot = _fetch_provider_snapshot_for_attempt(attempt, provider_lookup_fn=provider_lookup_fn)
    if snapshot is None:
        snapshot = _latest_event_snapshot_for_attempt(attempt)
    result = reconcile_attempt_with_snapshot(
        attempt=attempt,
        snapshot=snapshot,
        subscription=subscription,
    )
    db.session.add(attempt)
    if subscription:
        db.session.add(subscription)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    if apply_projection:
        _apply_projection_after_reconcile(
            attempt_id=int(attempt.id),
            payment_key=str(attempt.payment_key or ""),
            result=result,
        )
    return result


def reconcile_from_payment_event(
    *,
    payment_event_id: int | None = None,
    transmission_id: str | None = None,
    provider_lookup_fn: Callable[..., Mapping[str, Any]] | None = None,
    apply_projection: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    event = _lock_payment_event(payment_event_id=payment_event_id, transmission_id=transmission_id)
    if not event:
        raise BillingReconcileNotFound("재처리할 결제 이벤트를 찾지 못했어요.")
    now = _now()
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    lookup_snapshot = _snapshot_from_mapping(payload)
    result: dict[str, Any] | None = None

    attempt = None
    if str(event.related_payment_key or "").strip():
        attempt = _lock_payment_attempt_by_payment_key(str(event.related_payment_key))
    if not attempt and str(event.related_order_id or "").strip():
        attempt = _lock_payment_attempt_by_order(str(event.related_order_id))

    if not attempt:
        event.status = "failed"
        event.processed_at = now
        event.updated_at = now
        db.session.add(event)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return {
            "ok": False,
            "reason": "payment_attempt_not_found",
            "payment_event_id": int(event.id),
        }

    subscription = _lock_subscription(int(attempt.subscription_id or 0))
    provider_snapshot = lookup_snapshot
    if provider_snapshot is None:
        provider_snapshot = _fetch_provider_snapshot_for_attempt(attempt, provider_lookup_fn=provider_lookup_fn)
    if provider_snapshot is None:
        provider_snapshot = _latest_event_snapshot_for_attempt(attempt)

    result = reconcile_attempt_with_snapshot(
        attempt=attempt,
        snapshot=provider_snapshot,
        subscription=subscription,
        now=now,
    )

    attempt_status = str(result.get("status_after") or "")
    if attempt_status in TERMINAL_ATTEMPT_STATUSES:
        event.status = "applied"
    elif attempt_status == PAYMENT_STATUS_RECONCILE_NEEDED:
        event.status = "validated"
    else:
        event.status = "failed"
    event.processed_at = now
    event.updated_at = now

    db.session.add(event)
    db.session.add(attempt)
    if subscription:
        db.session.add(subscription)
    if commit:
        db.session.commit()
    else:
        db.session.flush()

    if apply_projection:
        _apply_projection_after_reconcile(
            attempt_id=int(attempt.id),
            order_id=str(attempt.order_id or ""),
            payment_key=str(attempt.payment_key or ""),
            payment_event_id=int(event.id),
            result=result,
        )

    result.update(
        {
            "ok": True,
            "payment_event_id": int(event.id),
            "payment_event_status": str(event.status),
        }
    )
    return result


def _apply_projection_after_reconcile(
    *,
    attempt_id: int,
    result: dict[str, Any],
    order_id: str | None = None,
    payment_key: str | None = None,
    payment_event_id: int | None = None,
) -> None:
    status_after = str(result.get("status_after") or "").strip().lower()
    if status_after not in TERMINAL_ATTEMPT_STATUSES:
        return

    from .projector import apply_entitlement_from_payment_attempt

    # source_id는 결제 시도 + 최종 상태 기준으로 고정해
    # success/webhook/수동재처리 경로가 달라도 동일한 projector 멱등키를 사용한다.
    source_id = f"attempt:{int(attempt_id)}|status:{status_after}"

    try:
        projection = apply_entitlement_from_payment_attempt(
            payment_attempt_id=int(attempt_id),
            source_type="reconcile_projection",
            source_id=source_id[:64],
            reason="reconcile 확정 반영",
            commit=True,
        )
        result["projection"] = projection
        result["projection_applied"] = bool(projection.get("applied"))
    except Exception as e:
        db.session.rollback()
        attempt = _lock_payment_attempt_by_order(order_id or "") if order_id else None
        if not attempt and payment_key:
            attempt = _lock_payment_attempt_by_payment_key(payment_key)
        if attempt and str(attempt.status or "").strip().lower() != PAYMENT_STATUS_RECONCILE_NEEDED:
            _mark_reconcile_needed(
                attempt,
                reason_code="projection_failed",
                reason_message="권한 반영 재시도가 필요해요.",
                now=_now(),
                force=True,
            )
            db.session.add(attempt)
            db.session.commit()
            result["status_after"] = PAYMENT_STATUS_RECONCILE_NEEDED
            result["reconcile_needed"] = True
            result["finalized"] = False
        result["projection_error"] = type(e).__name__
