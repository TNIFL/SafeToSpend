from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.extensions import db
from domain.models import BillingCustomer, BillingMethod, PaymentAttempt, Subscription, SubscriptionItem

from .constants import (
    ATTEMPT_TYPE_RECURRING,
    ATTEMPT_TYPE_RETRY,
    PAYMENT_STATUS_AUTHORIZED,
    PAYMENT_STATUS_CHARGE_STARTED,
    PAYMENT_STATUS_RECONCILED,
    PAYMENT_STATUS_RECONCILE_NEEDED,
    SUB_STATUS_ACTIVE,
    SUB_STATUS_CANCELED,
    SUB_STATUS_CANCEL_REQUESTED,
    SUB_STATUS_GRACE_STARTED,
    SUB_STATUS_PAST_DUE,
)
from .pricing import calculate_subscription_cycle_amount
from .projector import apply_entitlement_from_subscription_state
from .reconcile import BillingReconcileError, reconcile_by_order_id
from .service import build_payment_attempt_payload
from .state_machine import EVENT_SUB_CANCEL_EFFECTIVE, EVENT_SUB_MARK_PAST_DUE, transition_subscription_state
from .toss_client import build_billing_key_cipher_for_version, charge_billing_key


class BillingRecurringError(RuntimeError):
    pass


class BillingRecurringValidationError(BillingRecurringError):
    pass


@dataclass(frozen=True)
class RecurringCandidate:
    subscription_id: int
    user_pk: int
    due_kind: str  # recurring | retry
    reason: str
    cycle_start_at: datetime
    cycle_end_at: datetime
    next_billing_at: datetime | None
    amount_krw: int
    currency: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt(value: Any) -> datetime | None:
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


def _cycle_bounds(subscription: Subscription, *, now: datetime) -> tuple[datetime, datetime]:
    start = _to_dt(subscription.current_period_start) or _to_dt(subscription.billing_anchor_at)
    end = _to_dt(subscription.current_period_end) or _to_dt(subscription.next_billing_at)
    if start is None and end is None:
        start = now
        end = now + timedelta(days=30)
    elif start is None and end is not None:
        start = end - timedelta(days=30)
    elif start is not None and end is None:
        end = start + timedelta(days=30)
    if end is None or start is None:
        start = now
        end = now + timedelta(days=30)
    if end <= start:
        end = start + timedelta(days=30)
    return start, end


def _load_cycle_items(subscription_id: int) -> list[SubscriptionItem]:
    return (
        SubscriptionItem.query.filter(SubscriptionItem.subscription_id == int(subscription_id))
        .filter(SubscriptionItem.status.in_(("active", "pending")))
        .order_by(SubscriptionItem.effective_from.desc(), SubscriptionItem.id.desc())
        .all()
    )


def _list_candidate_subscriptions(*, subscription_id: int | None, limit: int) -> list[Subscription]:
    query = Subscription.query.filter(
        Subscription.status.in_(
            (
                SUB_STATUS_ACTIVE,
                SUB_STATUS_CANCEL_REQUESTED,
                SUB_STATUS_GRACE_STARTED,
                SUB_STATUS_PAST_DUE,
            )
        )
    ).order_by(Subscription.next_billing_at.asc().nulls_last(), Subscription.id.asc())
    if subscription_id:
        query = query.filter(Subscription.id == int(subscription_id))
    return query.limit(max(1, int(limit))).all()


def _resolve_active_billing_method(subscription: Subscription) -> BillingMethod | None:
    method_id = int(subscription.billing_method_id or 0)
    if method_id <= 0:
        return None
    return (
        BillingMethod.query.filter(BillingMethod.id == method_id)
        .filter(BillingMethod.user_pk == int(subscription.user_pk))
        .filter(BillingMethod.status == "active")
        .first()
    )


def _resolve_customer_key(*, method: BillingMethod | None, subscription: Subscription) -> str:
    method_customer_id = int(getattr(method, "billing_customer_id", 0) or 0)
    sub_customer_id = int(getattr(subscription, "billing_customer_id", 0) or 0)
    candidate_ids: list[int] = []
    if method_customer_id > 0:
        candidate_ids.append(method_customer_id)
    if sub_customer_id > 0 and sub_customer_id not in candidate_ids:
        candidate_ids.append(sub_customer_id)
    for customer_id in candidate_ids:
        row = (
            BillingCustomer.query.filter(BillingCustomer.id == int(customer_id))
            .filter(BillingCustomer.user_pk == int(subscription.user_pk))
            .first()
        )
        if not row:
            continue
        value = str(getattr(row, "customer_key", "") or "").strip()
        if value:
            return value
    return ""


def _find_cycle_attempt(
    *,
    subscription_id: int,
    cycle_start_at: datetime,
    cycle_end_at: datetime,
    attempt_types: tuple[str, ...],
    statuses: tuple[str, ...] | None = None,
) -> PaymentAttempt | None:
    query = (
        PaymentAttempt.query.filter(PaymentAttempt.subscription_id == int(subscription_id))
        .filter(PaymentAttempt.attempt_type.in_(attempt_types))
        .filter(PaymentAttempt.requested_at >= cycle_start_at)
        .filter(PaymentAttempt.requested_at < cycle_end_at)
    )
    if statuses:
        query = query.filter(PaymentAttempt.status.in_(statuses))
    return query.order_by(PaymentAttempt.id.desc()).first()


def evaluate_recurring_candidate(
    subscription: Subscription,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_dt = now or _now()
    status = str(subscription.status or "").strip().lower()
    cycle_start, cycle_end = _cycle_bounds(subscription, now=now_dt)
    next_billing_at = _to_dt(subscription.next_billing_at)

    if status == SUB_STATUS_CANCELED:
        return {"ok": False, "reason": "subscription_canceled"}
    if status == SUB_STATUS_PAST_DUE:
        return {"ok": False, "reason": "past_due_requires_retry_path"}

    cancel_effective_at = _to_dt(subscription.cancel_effective_at)
    if cancel_effective_at and now_dt >= cancel_effective_at:
        return {"ok": False, "reason": "cancel_effective_reached"}

    method = _resolve_active_billing_method(subscription)
    if not method:
        return {"ok": False, "reason": "billing_method_missing"}

    items = _load_cycle_items(int(subscription.id))
    pricing_cycle_at = next_billing_at or cycle_end
    amount_info = calculate_subscription_cycle_amount(
        subscription=subscription,
        items=items,
        cycle_at=pricing_cycle_at,
    )
    amount_krw = int(amount_info.get("total_amount_krw") or 0)
    if amount_krw <= 0:
        return {"ok": False, "reason": "non_positive_cycle_amount"}

    if status == SUB_STATUS_CANCEL_REQUESTED:
        return {"ok": False, "reason": "cancel_requested_period_end_only"}

    if status == SUB_STATUS_GRACE_STARTED:
        grace_until = _to_dt(subscription.grace_until)
        if grace_until and grace_until <= now_dt:
            return {"ok": False, "reason": "grace_expired"}
        existing_retry = _find_cycle_attempt(
            subscription_id=int(subscription.id),
            cycle_start_at=cycle_start,
            cycle_end_at=cycle_end,
            attempt_types=(ATTEMPT_TYPE_RETRY,),
            statuses=(
                PAYMENT_STATUS_CHARGE_STARTED,
                PAYMENT_STATUS_AUTHORIZED,
                PAYMENT_STATUS_RECONCILED,
                PAYMENT_STATUS_RECONCILE_NEEDED,
            ),
        )
        if existing_retry:
            return {"ok": False, "reason": "retry_attempt_already_exists"}
        return {
            "ok": True,
            "due_kind": "retry",
            "reason": "grace_retry_due",
            "subscription_id": int(subscription.id),
            "user_pk": int(subscription.user_pk),
            "cycle_start_at": cycle_start,
            "cycle_end_at": cycle_end,
            "next_billing_at": next_billing_at,
            "amount_krw": amount_krw,
            "currency": "KRW",
            "billing_method_id": int(method.id),
            "amount_breakdown": amount_info,
        }

    if next_billing_at is None:
        return {"ok": False, "reason": "next_billing_missing"}
    if next_billing_at > now_dt:
        return {"ok": False, "reason": "next_billing_in_future"}

    existing_cycle_attempt = _find_cycle_attempt(
        subscription_id=int(subscription.id),
        cycle_start_at=cycle_start,
        cycle_end_at=cycle_end,
        attempt_types=(ATTEMPT_TYPE_RECURRING, ATTEMPT_TYPE_RETRY),
    )
    if existing_cycle_attempt:
        return {"ok": False, "reason": "cycle_attempt_already_exists"}

    if status != SUB_STATUS_ACTIVE:
        return {"ok": False, "reason": f"unsupported_status:{status}"}

    return {
        "ok": True,
        "due_kind": "recurring",
        "reason": "regular_cycle_due",
        "subscription_id": int(subscription.id),
        "user_pk": int(subscription.user_pk),
        "cycle_start_at": cycle_start,
        "cycle_end_at": cycle_end,
        "next_billing_at": next_billing_at,
        "amount_krw": amount_krw,
        "currency": "KRW",
        "billing_method_id": int(method.id),
        "amount_breakdown": amount_info,
    }


def select_recurring_candidates(
    *,
    now: datetime | None = None,
    subscription_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    now_dt = now or _now()
    rows = _list_candidate_subscriptions(subscription_id=subscription_id, limit=limit)
    due_recurring: list[dict[str, Any]] = []
    due_retry: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for sub in rows:
        evaluated = evaluate_recurring_candidate(sub, now=now_dt)
        if bool(evaluated.get("ok")):
            if evaluated.get("due_kind") == "retry":
                due_retry.append(evaluated)
            else:
                due_recurring.append(evaluated)
            continue
        skipped.append(
            {
                "subscription_id": int(sub.id),
                "user_pk": int(sub.user_pk),
                "status": str(sub.status or ""),
                "reason": str(evaluated.get("reason") or "unknown"),
            }
        )

    return {
        "ok": True,
        "scanned": len(rows),
        "due_recurring": due_recurring,
        "due_retry": due_retry,
        "skipped": skipped,
        "now": now_dt.isoformat(),
    }


def _lock_subscription(subscription_id: int) -> Subscription | None:
    query = Subscription.query.filter_by(id=int(subscription_id))
    try:
        return query.with_for_update().first()
    except Exception:
        return query.first()


def _charge_subscription_candidate(
    *,
    candidate: dict[str, Any],
    dry_run: bool,
    idempotency_key_prefix: str,
) -> dict[str, Any]:
    subscription_id = int(candidate.get("subscription_id") or 0)
    subscription = _lock_subscription(subscription_id)
    if not subscription:
        return {"ok": False, "reason": "subscription_not_found", "subscription_id": subscription_id}

    # lock 이후 상태가 바뀌었을 수 있어 재평가
    reevaluated = evaluate_recurring_candidate(subscription, now=_now())
    if not bool(reevaluated.get("ok")):
        return {
            "ok": False,
            "reason": str(reevaluated.get("reason") or "revalidation_failed"),
            "subscription_id": subscription_id,
        }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "subscription_id": subscription_id,
            "due_kind": str(reevaluated.get("due_kind") or ""),
            "amount_krw": int(reevaluated.get("amount_krw") or 0),
            "currency": str(reevaluated.get("currency") or "KRW"),
            "reason": "dry_run",
        }

    due_kind = str(reevaluated.get("due_kind") or "recurring")
    attempt_type = ATTEMPT_TYPE_RETRY if due_kind == "retry" else ATTEMPT_TYPE_RECURRING
    amount_krw = int(reevaluated.get("amount_krw") or 0)
    payload = build_payment_attempt_payload(
        user_pk=int(subscription.user_pk),
        attempt_type=attempt_type,
        amount_krw=amount_krw,
        subscription_id=int(subscription.id),
        provider="toss",
        currency=str(reevaluated.get("currency") or "KRW"),
    )
    attempt = PaymentAttempt(**payload, created_at=_now(), updated_at=_now())
    db.session.add(attempt)
    db.session.commit()

    method = _resolve_active_billing_method(subscription)
    if not method:
        reconcile_by_order_id(
            order_id=str(attempt.order_id or ""),
            provider_snapshot={
                "provider_status": "failed",
                "order_id": str(attempt.order_id or ""),
                "total_amount": int(amount_krw),
                "currency": str(reevaluated.get("currency") or "KRW"),
                "fail_code": "billing_method_missing",
                "fail_message": "결제수단을 찾지 못했어요.",
            },
            apply_projection=True,
            commit=True,
        )
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "payment_attempt_id": int(attempt.id),
            "order_id": str(attempt.order_id or ""),
            "reason": "billing_method_missing",
        }

    try:
        cipher = build_billing_key_cipher_for_version(str(method.encryption_key_version or ""))
        billing_key_plain = cipher.decrypt(str(method.billing_key_enc or ""))
    except Exception:
        reconcile_by_order_id(
            order_id=str(attempt.order_id or ""),
            provider_snapshot={
                "provider_status": "failed",
                "order_id": str(attempt.order_id or ""),
                "total_amount": int(amount_krw),
                "currency": str(reevaluated.get("currency") or "KRW"),
                "fail_code": "billing_key_decrypt_failed",
                "fail_message": "결제수단 보안 정보를 확인하지 못했어요.",
            },
            apply_projection=True,
            commit=True,
        )
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "payment_attempt_id": int(attempt.id),
            "order_id": str(attempt.order_id or ""),
            "reason": "billing_key_decrypt_failed",
        }

    cycle_end = _to_dt(reevaluated.get("cycle_end_at"))
    cycle_key = cycle_end.strftime("%Y%m%d%H%M%S") if cycle_end else "na"
    idempotency_key = f"{idempotency_key_prefix}:{int(subscription.id)}:{cycle_key}"[:64]
    customer_key = _resolve_customer_key(method=method, subscription=subscription)
    if not customer_key:
        reconcile_by_order_id(
            order_id=str(attempt.order_id or ""),
            provider_snapshot={
                "provider_status": "failed",
                "order_id": str(attempt.order_id or ""),
                "total_amount": int(amount_krw),
                "currency": str(reevaluated.get("currency") or "KRW"),
                "fail_code": "billing_customer_missing",
                "fail_message": "결제 고객 정보를 찾지 못했어요.",
            },
            apply_projection=True,
            commit=True,
        )
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "payment_attempt_id": int(attempt.id),
            "order_id": str(attempt.order_id or ""),
            "reason": "billing_customer_missing",
        }

    try:
        response = charge_billing_key(
            billing_key=billing_key_plain,
            customer_key=customer_key,
            amount_krw=amount_krw,
            order_id=str(attempt.order_id or ""),
            order_name="쓸수있어 정기 결제",
            idempotency_key=idempotency_key,
        )
    except Exception as e:
        reconcile_by_order_id(
            order_id=str(attempt.order_id or ""),
            provider_snapshot={
                "provider_status": "failed",
                "order_id": str(attempt.order_id or ""),
                "total_amount": int(amount_krw),
                "currency": str(reevaluated.get("currency") or "KRW"),
                "fail_code": "charge_request_failed",
                "fail_message": str(type(e).__name__),
            },
            apply_projection=True,
            commit=True,
        )
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "payment_attempt_id": int(attempt.id),
            "order_id": str(attempt.order_id or ""),
            "reason": "charge_request_failed",
        }

    reconciled = reconcile_by_order_id(
        order_id=str(attempt.order_id or ""),
        provider_snapshot=response,
        apply_projection=True,
        commit=True,
    )
    return {
        "ok": True,
        "subscription_id": subscription_id,
        "payment_attempt_id": int(attempt.id),
        "order_id": str(attempt.order_id or ""),
        "status_after": str(reconciled.get("status_after") or ""),
        "reconciled": bool(reconciled.get("reconciled")),
        "reconcile_needed": bool(reconciled.get("reconcile_needed")),
        "due_kind": due_kind,
    }


def run_recurring_batch(
    *,
    now: datetime | None = None,
    limit: int = 100,
    dry_run: bool = False,
    subscription_id: int | None = None,
    include_retry: bool = True,
) -> dict[str, Any]:
    now_dt = now or _now()
    selection = select_recurring_candidates(now=now_dt, subscription_id=subscription_id, limit=limit)
    due = list(selection.get("due_recurring") or [])
    if include_retry:
        due.extend(selection.get("due_retry") or [])

    results: list[dict[str, Any]] = []
    for candidate in due:
        try:
            result = _charge_subscription_candidate(
                candidate=candidate,
                dry_run=bool(dry_run),
                idempotency_key_prefix=("retry" if candidate.get("due_kind") == "retry" else "recurring"),
            )
        except BillingReconcileError as e:
            result = {
                "ok": False,
                "subscription_id": int(candidate.get("subscription_id") or 0),
                "reason": f"reconcile_error:{type(e).__name__}",
            }
        except Exception as e:
            result = {
                "ok": False,
                "subscription_id": int(candidate.get("subscription_id") or 0),
                "reason": f"unexpected:{type(e).__name__}",
            }
        results.append(result)

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned": int(selection.get("scanned") or 0),
        "due_recurring_count": len(selection.get("due_recurring") or []),
        "due_retry_count": len(selection.get("due_retry") or []),
        "executed_count": len(results),
        "success_count": sum(1 for r in results if bool(r.get("ok"))),
        "failure_count": sum(1 for r in results if not bool(r.get("ok"))),
        "skipped_count": len(selection.get("skipped") or []),
        "skipped": selection.get("skipped") or [],
        "results": results,
    }


def run_retry_batch(
    *,
    now: datetime | None = None,
    limit: int = 100,
    dry_run: bool = False,
    subscription_id: int | None = None,
) -> dict[str, Any]:
    selection = select_recurring_candidates(now=now, subscription_id=subscription_id, limit=limit)
    retry_only = list(selection.get("due_retry") or [])
    results: list[dict[str, Any]] = []
    for candidate in retry_only:
        results.append(
            _charge_subscription_candidate(
                candidate=candidate,
                dry_run=bool(dry_run),
                idempotency_key_prefix="retry",
            )
        )
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "due_retry_count": len(retry_only),
        "executed_count": len(results),
        "success_count": sum(1 for r in results if bool(r.get("ok"))),
        "failure_count": sum(1 for r in results if not bool(r.get("ok"))),
        "results": results,
    }


def run_grace_expiry(
    *,
    now: datetime | None = None,
    subscription_id: int | None = None,
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    now_dt = now or _now()
    query = Subscription.query.filter(Subscription.status == SUB_STATUS_GRACE_STARTED)
    if subscription_id:
        query = query.filter(Subscription.id == int(subscription_id))
    query = query.order_by(Subscription.grace_until.asc().nulls_last(), Subscription.id.asc())
    rows = query.limit(max(1, int(limit))).all()

    results: list[dict[str, Any]] = []
    for row in rows:
        sid = int(row.id)
        grace_until = _to_dt(row.grace_until)
        if not grace_until or grace_until > now_dt:
            results.append({"ok": False, "subscription_id": sid, "reason": "grace_not_expired"})
            continue
        locked = _lock_subscription(sid)
        if not locked:
            results.append({"ok": False, "subscription_id": sid, "reason": "subscription_not_found"})
            continue
        if str(locked.status or "").strip().lower() != SUB_STATUS_GRACE_STARTED:
            results.append({"ok": False, "subscription_id": sid, "reason": "status_changed"})
            continue
        if dry_run:
            results.append({"ok": True, "dry_run": True, "subscription_id": sid, "reason": "would_mark_past_due"})
            continue
        try:
            locked.status = transition_subscription_state(str(locked.status or ""), EVENT_SUB_MARK_PAST_DUE)
        except Exception:
            results.append({"ok": False, "subscription_id": sid, "reason": "transition_failed"})
            continue
        locked.updated_at = now_dt
        db.session.add(locked)
        db.session.commit()
        projection = apply_entitlement_from_subscription_state(
            subscription_id=sid,
            source_type="grace_expiry",
            source_id=f"sub:{sid}|grace:{grace_until.isoformat()}"[:64],
            reason="grace 만료에 따른 past_due 전환",
            commit=True,
        )
        results.append(
            {
                "ok": True,
                "subscription_id": sid,
                "status_after": str(locked.status or ""),
                "projection_applied": bool(projection.get("applied")),
            }
        )

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned": len(rows),
        "processed": len(results),
        "results": results,
    }


def run_cancel_effective(
    *,
    now: datetime | None = None,
    subscription_id: int | None = None,
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    now_dt = now or _now()
    query = Subscription.query.filter(Subscription.status == SUB_STATUS_CANCEL_REQUESTED)
    if subscription_id:
        query = query.filter(Subscription.id == int(subscription_id))
    query = query.order_by(Subscription.cancel_effective_at.asc().nulls_last(), Subscription.id.asc())
    rows = query.limit(max(1, int(limit))).all()

    results: list[dict[str, Any]] = []
    for row in rows:
        sid = int(row.id)
        cancel_effective_at = _to_dt(row.cancel_effective_at)
        if not cancel_effective_at:
            results.append({"ok": False, "subscription_id": sid, "reason": "cancel_effective_missing"})
            continue
        if cancel_effective_at > now_dt:
            results.append({"ok": False, "subscription_id": sid, "reason": "cancel_not_due"})
            continue
        locked = _lock_subscription(sid)
        if not locked:
            results.append({"ok": False, "subscription_id": sid, "reason": "subscription_not_found"})
            continue
        if str(locked.status or "").strip().lower() != SUB_STATUS_CANCEL_REQUESTED:
            results.append({"ok": False, "subscription_id": sid, "reason": "status_changed"})
            continue
        if dry_run:
            results.append({"ok": True, "dry_run": True, "subscription_id": sid, "reason": "would_cancel_effective"})
            continue
        try:
            locked.status = transition_subscription_state(str(locked.status or ""), EVENT_SUB_CANCEL_EFFECTIVE)
        except Exception:
            results.append({"ok": False, "subscription_id": sid, "reason": "transition_failed"})
            continue
        locked.canceled_at = locked.canceled_at or now_dt
        locked.updated_at = now_dt
        locked.next_billing_at = None
        db.session.add(locked)
        db.session.commit()
        projection = apply_entitlement_from_subscription_state(
            subscription_id=sid,
            source_type="cancel_effective",
            source_id=f"sub:{sid}|cancel:{cancel_effective_at.isoformat()}"[:64],
            reason="기간 종료 해지 반영",
            commit=True,
        )
        results.append(
            {
                "ok": True,
                "subscription_id": sid,
                "status_after": str(locked.status or ""),
                "projection_applied": bool(projection.get("applied")),
            }
        )

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned": len(rows),
        "processed": len(results),
        "results": results,
    }
