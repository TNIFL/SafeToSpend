from __future__ import annotations

from .constants import (
    PAYMENT_ATTEMPT_STATUSES,
    PAYMENT_STATUS_AUTHORIZED,
    PAYMENT_STATUS_CANCELED,
    PAYMENT_STATUS_CHARGE_STARTED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_RECONCILED,
    PAYMENT_STATUS_RECONCILE_NEEDED,
    SUBSCRIPTION_STATUSES,
    SUB_STATUS_ACTIVE,
    SUB_STATUS_CANCEL_REQUESTED,
    SUB_STATUS_CANCELED,
    SUB_STATUS_GRACE_STARTED,
    SUB_STATUS_PAST_DUE,
    SUB_STATUS_PENDING_ACTIVATION,
)


class StateTransitionError(ValueError):
    pass


EVENT_SUB_ACTIVATE = "activate"
EVENT_SUB_REQUEST_CANCEL = "request_cancel"
EVENT_SUB_CANCEL_EFFECTIVE = "cancel_effective"
EVENT_SUB_START_GRACE = "start_grace"
EVENT_SUB_MARK_PAST_DUE = "mark_past_due"
EVENT_SUB_RECOVER_PAYMENT = "recover_payment"

EVENT_PAYMARK_AUTHORIZED = "mark_authorized"
EVENT_PAYMARK_FAILED = "mark_failed"
EVENT_PAYMARK_RECONCILED = "mark_reconciled"
EVENT_PAYMARK_RECONCILE_NEEDED = "mark_reconcile_needed"
EVENT_PAYMARK_CANCELED = "mark_canceled"


_SUB_TRANSITIONS: dict[tuple[str, str], str] = {
    (SUB_STATUS_PENDING_ACTIVATION, EVENT_SUB_ACTIVATE): SUB_STATUS_ACTIVE,
    (SUB_STATUS_ACTIVE, EVENT_SUB_REQUEST_CANCEL): SUB_STATUS_CANCEL_REQUESTED,
    (SUB_STATUS_CANCEL_REQUESTED, EVENT_SUB_CANCEL_EFFECTIVE): SUB_STATUS_CANCELED,
    (SUB_STATUS_ACTIVE, EVENT_SUB_START_GRACE): SUB_STATUS_GRACE_STARTED,
    (SUB_STATUS_GRACE_STARTED, EVENT_SUB_MARK_PAST_DUE): SUB_STATUS_PAST_DUE,
    (SUB_STATUS_GRACE_STARTED, EVENT_SUB_RECOVER_PAYMENT): SUB_STATUS_ACTIVE,
    (SUB_STATUS_PAST_DUE, EVENT_SUB_RECOVER_PAYMENT): SUB_STATUS_ACTIVE,
}

_PAYMENT_TRANSITIONS: dict[tuple[str, str], str] = {
    (PAYMENT_STATUS_CHARGE_STARTED, EVENT_PAYMARK_AUTHORIZED): PAYMENT_STATUS_AUTHORIZED,
    (PAYMENT_STATUS_CHARGE_STARTED, EVENT_PAYMARK_FAILED): PAYMENT_STATUS_FAILED,
    (PAYMENT_STATUS_CHARGE_STARTED, EVENT_PAYMARK_CANCELED): PAYMENT_STATUS_CANCELED,
    (PAYMENT_STATUS_AUTHORIZED, EVENT_PAYMARK_RECONCILED): PAYMENT_STATUS_RECONCILED,
    (PAYMENT_STATUS_AUTHORIZED, EVENT_PAYMARK_RECONCILE_NEEDED): PAYMENT_STATUS_RECONCILE_NEEDED,
    (PAYMENT_STATUS_AUTHORIZED, EVENT_PAYMARK_FAILED): PAYMENT_STATUS_FAILED,
    (PAYMENT_STATUS_RECONCILE_NEEDED, EVENT_PAYMARK_RECONCILED): PAYMENT_STATUS_RECONCILED,
    (PAYMENT_STATUS_RECONCILE_NEEDED, EVENT_PAYMARK_FAILED): PAYMENT_STATUS_FAILED,
}


def transition_subscription_state(current_status: str, event: str) -> str:
    current = str(current_status or "").strip().lower()
    ev = str(event or "").strip().lower()
    if current not in SUBSCRIPTION_STATUSES:
        raise StateTransitionError(f"알 수 없는 subscription 상태예요: {current_status}")
    target = _SUB_TRANSITIONS.get((current, ev))
    if not target:
        raise StateTransitionError(f"허용되지 않은 subscription 전이예요: {current} -> {ev}")
    return target


def transition_payment_attempt_state(current_status: str, event: str) -> str:
    current = str(current_status or "").strip().lower()
    ev = str(event or "").strip().lower()
    if current not in PAYMENT_ATTEMPT_STATUSES:
        raise StateTransitionError(f"알 수 없는 payment_attempt 상태예요: {current_status}")
    target = _PAYMENT_TRANSITIONS.get((current, ev))
    if not target:
        raise StateTransitionError(f"허용되지 않은 payment_attempt 전이예요: {current} -> {ev}")
    return target
