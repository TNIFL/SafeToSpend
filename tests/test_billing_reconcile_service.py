from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from services.billing.constants import (
    PAYMENT_STATUS_CHARGE_STARTED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_RECONCILE_NEEDED,
    SUB_STATUS_GRACE_STARTED,
    SUB_STATUS_PENDING_ACTIVATION,
)
from services.billing.reconcile import (
    ReconcileSnapshot,
    reconcile_attempt_with_snapshot,
    reconcile_by_order_id,
    reconcile_from_payment_event,
)


def _attempt(**kwargs):
    base = {
        "id": 1,
        "status": PAYMENT_STATUS_CHARGE_STARTED,
        "amount_krw": 6900,
        "currency": "KRW",
        "order_id": "ord_test_1",
        "payment_key": None,
        "provider": "toss",
        "subscription_id": None,
        "authorized_at": None,
        "reconciled_at": None,
        "failed_at": None,
        "fail_code": None,
        "fail_message_norm": None,
        "updated_at": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _subscription(**kwargs):
    now = datetime.now(timezone.utc)
    base = {
        "id": 99,
        "status": SUB_STATUS_PENDING_ACTIVATION,
        "billing_anchor_at": now - timedelta(days=30),
        "current_period_start": now - timedelta(days=30),
        "current_period_end": now,
        "next_billing_at": now,
        "grace_until": None,
        "retry_count": 0,
        "last_paid_at": None,
        "last_failed_at": None,
        "cancel_effective_at": None,
        "canceled_at": None,
        "updated_at": now,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class BillingReconcileServiceTest(unittest.TestCase):
    def test_reconcile_success_transitions_to_reconciled(self) -> None:
        attempt = _attempt(fail_code="x", fail_message_norm="y")
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=datetime.now(timezone.utc),
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot)
        self.assertEqual(result["status_after"], "reconciled")
        self.assertEqual(str(attempt.payment_key), "pay_test_1")
        self.assertIsNotNone(attempt.authorized_at)
        self.assertIsNotNone(attempt.reconciled_at)
        self.assertIsNone(attempt.fail_code)
        self.assertIsNone(attempt.fail_message_norm)

    def test_reconcile_amount_mismatch_moves_to_reconcile_needed(self) -> None:
        attempt = _attempt()
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=7000,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_RECONCILE_NEEDED)
        self.assertEqual(str(attempt.fail_code), "amount_or_currency_mismatch")

    def test_reconcile_failed_snapshot_marks_failed(self) -> None:
        attempt = _attempt()
        snapshot = ReconcileSnapshot(
            provider_status="failed",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code="PG_FAIL",
            fail_message="declined",
            approved_at=None,
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_FAILED)
        self.assertEqual(str(attempt.fail_code), "PG_FAIL")
        self.assertEqual(str(attempt.fail_message_norm), "declined")
        self.assertIsNotNone(attempt.failed_at)

    def test_pending_subscription_activates_on_success(self) -> None:
        attempt = _attempt(subscription_id=10)
        sub = _subscription(status=SUB_STATUS_PENDING_ACTIVATION)
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot, subscription=sub)
        self.assertEqual(result["status_after"], "reconciled")
        self.assertEqual(str(sub.status), "active")
        self.assertIsNotNone(sub.last_paid_at)
        self.assertEqual(int(sub.retry_count), 0)

    def test_grace_subscription_can_move_to_past_due_even_when_attempt_finalized(self) -> None:
        now = datetime.now(timezone.utc)
        attempt = _attempt(status=PAYMENT_STATUS_FAILED, failed_at=now - timedelta(days=4))
        sub = _subscription(
            status=SUB_STATUS_GRACE_STARTED,
            grace_until=now - timedelta(minutes=1),
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=None, subscription=sub, now=now)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_FAILED)
        self.assertEqual(str(sub.status), "past_due")

    def test_reconcile_by_order_id_commits_and_returns_result(self) -> None:
        attempt = _attempt()
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_attempt_by_order", return_value=attempt),
            patch("services.billing.reconcile._lock_subscription", return_value=None),
            patch("services.billing.reconcile._fetch_provider_snapshot_for_attempt", return_value=snapshot),
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit") as commit_mock,
        ):
            result = reconcile_by_order_id(order_id="ord_test_1")
        self.assertEqual(result["status_after"], "reconciled")
        commit_mock.assert_called_once()

    def test_reconcile_from_event_without_target_attempt_is_safe(self) -> None:
        event = SimpleNamespace(
            id=10,
            status="received",
            payload_json={},
            related_order_id=None,
            related_payment_key=None,
            processed_at=None,
            updated_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_event", return_value=event),
            patch("services.billing.reconcile._lock_payment_attempt_by_payment_key", return_value=None),
            patch("services.billing.reconcile._lock_payment_attempt_by_order", return_value=None),
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit") as commit_mock,
        ):
            result = reconcile_from_payment_event(payment_event_id=10)
        self.assertFalse(result["ok"])
        self.assertEqual(str(event.status), "failed")
        commit_mock.assert_called_once()

    def test_reconcile_from_event_triggers_projection(self) -> None:
        attempt = _attempt()
        event = SimpleNamespace(
            id=21,
            status="received",
            payload_json={"status": "done"},
            related_order_id="ord_test_1",
            related_payment_key=None,
            processed_at=None,
            updated_at=None,
        )
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_event", return_value=event),
            patch("services.billing.reconcile._lock_payment_attempt_by_payment_key", return_value=None),
            patch("services.billing.reconcile._lock_payment_attempt_by_order", return_value=attempt),
            patch("services.billing.reconcile._lock_subscription", return_value=None),
            patch("services.billing.reconcile._fetch_provider_snapshot_for_attempt", return_value=snapshot),
            patch(
                "services.billing.projector.apply_entitlement_from_payment_attempt",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ) as projector_mock,
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit"),
        ):
            result = reconcile_from_payment_event(payment_event_id=21, apply_projection=True)
        self.assertTrue(result["ok"])
        self.assertEqual(str(event.status), "applied")
        self.assertTrue(result.get("projection_applied"))
        projector_mock.assert_called_once()

    def test_reconcile_projection_failure_marks_attempt_reconcile_needed(self) -> None:
        attempt = _attempt()
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_attempt_by_order", side_effect=[attempt, attempt]),
            patch("services.billing.reconcile._lock_subscription", return_value=None),
            patch("services.billing.reconcile._fetch_provider_snapshot_for_attempt", return_value=snapshot),
            patch(
                "services.billing.projector.apply_entitlement_from_payment_attempt",
                side_effect=RuntimeError("projection_failed"),
            ),
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit"),
            patch("services.billing.reconcile.db.session.rollback"),
        ):
            result = reconcile_by_order_id(order_id="ord_test_1", apply_projection=True)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_RECONCILE_NEEDED)
        self.assertEqual(str(attempt.status), PAYMENT_STATUS_RECONCILE_NEEDED)
        self.assertEqual(str(attempt.fail_code), "projection_failed")

    def test_reconcile_projection_source_id_is_stable_per_attempt_status(self) -> None:
        attempt = _attempt(id=44)
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_44",
            payment_key="pay_test_44",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_attempt_by_order", return_value=attempt),
            patch("services.billing.reconcile._lock_subscription", return_value=None),
            patch("services.billing.reconcile._fetch_provider_snapshot_for_attempt", return_value=snapshot),
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit"),
            patch(
                "services.billing.projector.apply_entitlement_from_payment_attempt",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ) as projector_mock,
        ):
            result = reconcile_by_order_id(order_id="ord_test_44", apply_projection=True)
        self.assertTrue(result["reconciled"])
        projector_mock.assert_called_once()
        self.assertEqual(
            projector_mock.call_args.kwargs.get("source_id"),
            "attempt:44|status:reconciled",
        )

    def test_reconcile_needed_does_not_apply_projection(self) -> None:
        attempt = _attempt(id=55, order_id="ord_test_55")
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_55",
            payment_key="pay_test_55",
            amount_krw=7000,  # 내부 금액과 불일치
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=None,
        )
        with (
            patch("services.billing.reconcile._lock_payment_attempt_by_order", return_value=attempt),
            patch("services.billing.reconcile._lock_subscription", return_value=None),
            patch("services.billing.reconcile._fetch_provider_snapshot_for_attempt", return_value=snapshot),
            patch("services.billing.reconcile.db.session.add"),
            patch("services.billing.reconcile.db.session.commit"),
            patch(
                "services.billing.projector.apply_entitlement_from_payment_attempt",
                return_value={"ok": True, "applied": True},
            ) as projector_mock,
        ):
            result = reconcile_by_order_id(order_id="ord_test_55", apply_projection=True)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_RECONCILE_NEEDED)
        projector_mock.assert_not_called()

    def test_recurring_success_advances_subscription_cycle(self) -> None:
        now = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        attempt = _attempt(
            attempt_type="recurring",
            subscription_id=10,
            status=PAYMENT_STATUS_CHARGE_STARTED,
        )
        sub = _subscription(
            status="active",
            current_period_start=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            next_billing_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
        )
        snapshot = ReconcileSnapshot(
            provider_status="done",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6900,
            currency="KRW",
            fail_code=None,
            fail_message=None,
            approved_at=now,
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot, subscription=sub, now=now)
        self.assertEqual(result["status_after"], "reconciled")
        self.assertEqual(str(sub.status), "active")
        self.assertEqual(sub.current_period_start, datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(sub.current_period_end, datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(sub.next_billing_at, datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc))

    def test_addon_failure_does_not_start_grace(self) -> None:
        now = datetime.now(timezone.utc)
        attempt = _attempt(
            attempt_type="addon_proration",
            subscription_id=10,
            status=PAYMENT_STATUS_CHARGE_STARTED,
        )
        sub = _subscription(status="active")
        snapshot = ReconcileSnapshot(
            provider_status="failed",
            order_id="ord_test_1",
            payment_key="pay_test_1",
            amount_krw=6000,
            currency="KRW",
            fail_code="PG_FAIL",
            fail_message="declined",
            approved_at=None,
        )
        result = reconcile_attempt_with_snapshot(attempt=attempt, snapshot=snapshot, subscription=sub, now=now)
        self.assertEqual(result["status_after"], PAYMENT_STATUS_FAILED)
        self.assertEqual(str(sub.status), "active")
        self.assertIsNone(sub.grace_until)


if __name__ == "__main__":
    unittest.main()
