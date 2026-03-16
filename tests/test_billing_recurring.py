from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from services.billing.pricing import calculate_subscription_cycle_amount
from services.billing.recurring import (
    evaluate_recurring_candidate,
    run_cancel_effective,
    run_grace_expiry,
    run_recurring_batch,
    run_retry_batch,
)


def _subscription(**kwargs) -> SimpleNamespace:
    now = datetime(2026, 3, 10, tzinfo=timezone.utc)
    base = {
        "id": 10,
        "user_pk": 1,
        "status": "active",
        "billing_anchor_at": now - timedelta(days=30),
        "current_period_start": now - timedelta(days=30),
        "current_period_end": now,
        "next_billing_at": now,
        "grace_until": now + timedelta(days=1),
        "billing_method_id": 3,
        "billing_customer_id": 5,
        "cancel_effective_at": None,
        "canceled_at": None,
        "updated_at": now,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _item(**kwargs) -> SimpleNamespace:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    base = {
        "id": 1,
        "item_type": "plan_base",
        "item_code": "basic",
        "quantity": 1,
        "unit_price_krw": 6900,
        "amount_krw": 6900,
        "status": "active",
        "effective_from": now - timedelta(days=1),
        "effective_to": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _DummyColumn:
    def asc(self):
        return self

    def nulls_last(self):
        return self


class BillingRecurringTest(unittest.TestCase):
    def test_cycle_amount_includes_base_and_addon(self) -> None:
        subscription = _subscription()
        items = [
            _item(item_type="plan_base", item_code="basic", quantity=1, unit_price_krw=6900, amount_krw=6900),
            _item(id=2, item_type="addon_account_slot", item_code="addon_account_slot", quantity=2, unit_price_krw=3000, amount_krw=6000),
        ]
        result = calculate_subscription_cycle_amount(subscription=subscription, items=items, cycle_at=datetime(2026, 3, 10, tzinfo=timezone.utc))
        self.assertEqual(int(result["plan_amount_krw"]), 6900)
        self.assertEqual(int(result["addon_quantity"]), 2)
        self.assertEqual(int(result["addon_amount_krw"]), 6000)
        self.assertEqual(int(result["total_amount_krw"]), 12900)

    def test_cycle_amount_excludes_ended_addon_on_next_cycle(self) -> None:
        subscription = _subscription()
        ended_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
        items = [
            _item(item_type="plan_base", item_code="pro", unit_price_krw=12900, amount_krw=12900),
            _item(
                id=3,
                item_type="addon_account_slot",
                item_code="addon_account_slot",
                quantity=1,
                unit_price_krw=3000,
                amount_krw=3000,
                effective_to=ended_at,
            ),
        ]
        current = calculate_subscription_cycle_amount(
            subscription=subscription,
            items=items,
            cycle_at=datetime(2026, 3, 15, tzinfo=timezone.utc),
        )
        next_cycle = calculate_subscription_cycle_amount(
            subscription=subscription,
            items=items,
            cycle_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(int(current["total_amount_krw"]), 15900)
        self.assertEqual(int(next_cycle["total_amount_krw"]), 12900)

    def test_evaluate_candidate_active_due(self) -> None:
        sub = _subscription(status="active", next_billing_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        with (
            patch("services.billing.recurring._resolve_active_billing_method", return_value=SimpleNamespace(id=3)),
            patch("services.billing.recurring._load_cycle_items", return_value=[_item()]),
            patch("services.billing.recurring._find_cycle_attempt", return_value=None),
        ):
            result = evaluate_recurring_candidate(sub, now=now)
        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(str(result.get("due_kind")), "recurring")

    def test_evaluate_candidate_future_due_is_skipped(self) -> None:
        sub = _subscription(status="active", next_billing_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        with (
            patch("services.billing.recurring._resolve_active_billing_method", return_value=SimpleNamespace(id=3)),
            patch("services.billing.recurring._load_cycle_items", return_value=[_item()]),
        ):
            result = evaluate_recurring_candidate(sub, now=now)
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("reason")), "next_billing_in_future")

    def test_evaluate_candidate_requires_billing_method(self) -> None:
        sub = _subscription(status="active")
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        with patch("services.billing.recurring._resolve_active_billing_method", return_value=None):
            result = evaluate_recurring_candidate(sub, now=now)
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("reason")), "billing_method_missing")

    def test_evaluate_candidate_grace_retry_due(self) -> None:
        sub = _subscription(
            status="grace_started",
            next_billing_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            grace_until=datetime(2026, 3, 12, tzinfo=timezone.utc),
        )
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        with (
            patch("services.billing.recurring._resolve_active_billing_method", return_value=SimpleNamespace(id=3)),
            patch("services.billing.recurring._load_cycle_items", return_value=[_item()]),
            patch("services.billing.recurring._find_cycle_attempt", return_value=None),
        ):
            result = evaluate_recurring_candidate(sub, now=now)
        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(str(result.get("due_kind")), "retry")

    def test_run_recurring_batch_aggregates_results(self) -> None:
        selection = {
            "scanned": 2,
            "due_recurring": [{"subscription_id": 1, "due_kind": "recurring"}],
            "due_retry": [],
            "skipped": [{"subscription_id": 2, "reason": "next_billing_in_future"}],
        }
        with (
            patch("services.billing.recurring.select_recurring_candidates", return_value=selection),
            patch("services.billing.recurring._charge_subscription_candidate", return_value={"ok": True, "subscription_id": 1}),
        ):
            result = run_recurring_batch(dry_run=True, include_retry=False)
        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(int(result.get("executed_count") or 0), 1)
        self.assertEqual(int(result.get("success_count") or 0), 1)
        self.assertEqual(int(result.get("skipped_count") or 0), 1)

    def test_run_retry_batch_uses_retry_candidates_only(self) -> None:
        selection = {
            "due_retry": [{"subscription_id": 11, "due_kind": "retry"}],
            "due_recurring": [{"subscription_id": 99, "due_kind": "recurring"}],
        }
        with (
            patch("services.billing.recurring.select_recurring_candidates", return_value=selection),
            patch("services.billing.recurring._charge_subscription_candidate", return_value={"ok": True, "subscription_id": 11}),
        ):
            result = run_retry_batch(dry_run=True)
        self.assertEqual(int(result.get("due_retry_count") or 0), 1)
        self.assertEqual(int(result.get("executed_count") or 0), 1)

    def test_grace_expiry_marks_past_due_and_projects(self) -> None:
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        row = _subscription(id=45, status="grace_started", grace_until=now - timedelta(minutes=1))
        col = _DummyColumn()
        subscription_cls = SimpleNamespace(
            query=_FakeQuery([row]),
            status=col,
            grace_until=col,
            id=col,
            cancel_effective_at=col,
        )
        with (
            patch("services.billing.recurring.Subscription", new=subscription_cls),
            patch("services.billing.recurring._lock_subscription", return_value=row),
            patch("services.billing.recurring.transition_subscription_state", return_value="past_due"),
            patch("services.billing.recurring.apply_entitlement_from_subscription_state", return_value={"applied": True}),
            patch("services.billing.recurring.db.session.add"),
            patch("services.billing.recurring.db.session.commit"),
        ):
            result = run_grace_expiry(now=now, dry_run=False)
        self.assertEqual(int(result.get("processed") or 0), 1)
        first = (result.get("results") or [{}])[0]
        self.assertTrue(bool(first.get("ok")))
        self.assertEqual(str(first.get("status_after")), "past_due")

    def test_cancel_effective_marks_canceled_and_projects(self) -> None:
        now = datetime(2026, 3, 10, tzinfo=timezone.utc)
        row = _subscription(
            id=46,
            status="cancel_requested",
            cancel_effective_at=now - timedelta(minutes=1),
            next_billing_at=now,
        )
        col = _DummyColumn()
        subscription_cls = SimpleNamespace(
            query=_FakeQuery([row]),
            status=col,
            grace_until=col,
            id=col,
            cancel_effective_at=col,
        )
        with (
            patch("services.billing.recurring.Subscription", new=subscription_cls),
            patch("services.billing.recurring._lock_subscription", return_value=row),
            patch("services.billing.recurring.transition_subscription_state", return_value="canceled"),
            patch("services.billing.recurring.apply_entitlement_from_subscription_state", return_value={"applied": True}),
            patch("services.billing.recurring.db.session.add"),
            patch("services.billing.recurring.db.session.commit"),
        ):
            result = run_cancel_effective(now=now, dry_run=False)
        self.assertEqual(int(result.get("processed") or 0), 1)
        first = (result.get("results") or [{}])[0]
        self.assertTrue(bool(first.get("ok")))
        self.assertEqual(str(first.get("status_after")), "canceled")


if __name__ == "__main__":
    unittest.main()
