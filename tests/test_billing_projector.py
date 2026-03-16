from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from services.billing.projector import (
    BillingProjectorError,
    EntitlementProjection,
    apply_entitlement_from_payment_attempt,
    apply_entitlement_from_subscription_state,
    derive_projection_from_subscription,
    reproject_entitlement_for_user,
)


def _sub(**kwargs):
    base = {
        "id": 1,
        "user_pk": 7,
        "status": "active",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _item(**kwargs):
    now = datetime.now(timezone.utc)
    base = {
        "item_type": "plan_base",
        "item_code": "basic",
        "quantity": 1,
        "status": "active",
        "effective_from": now,
        "effective_to": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class BillingProjectorTest(unittest.TestCase):
    def test_derive_projection_active_subscription(self) -> None:
        sub = _sub(status="active")
        with patch(
            "services.billing.projector._load_subscription_items",
            return_value=[
                _item(item_type="plan_base", item_code="pro"),
                _item(item_type="addon_account_slot", item_code="addon_account_slot", quantity=2),
            ],
        ):
            projection = derive_projection_from_subscription(sub)
        self.assertEqual(projection, EntitlementProjection(plan_code="pro", plan_status="active", extra_account_slots=2))

    def test_derive_projection_canceled_subscription(self) -> None:
        sub = _sub(status="canceled")
        with patch(
            "services.billing.projector._load_subscription_items",
            return_value=[_item(item_type="plan_base", item_code="pro")],
        ):
            projection = derive_projection_from_subscription(sub)
        self.assertEqual(projection, EntitlementProjection(plan_code="free", plan_status="active", extra_account_slots=0))

    def test_derive_projection_past_due_subscription(self) -> None:
        sub = _sub(status="past_due")
        with patch(
            "services.billing.projector._load_subscription_items",
            return_value=[_item(item_type="plan_base", item_code="basic")],
        ):
            projection = derive_projection_from_subscription(sub)
        self.assertEqual(projection, EntitlementProjection(plan_code="basic", plan_status="past_due", extra_account_slots=0))

    def test_apply_entitlement_from_payment_attempt_requires_subscription(self) -> None:
        attempt = SimpleNamespace(id=11, subscription_id=None, user_pk=1)
        with patch("services.billing.projector._lock_payment_attempt", return_value=attempt):
            with self.assertRaises(BillingProjectorError):
                apply_entitlement_from_payment_attempt(payment_attempt_id=11)

    def test_apply_entitlement_from_payment_attempt_upgrade_marks_intent_completed(self) -> None:
        now = datetime.now(timezone.utc)
        attempt = SimpleNamespace(
            id=31,
            subscription_id=5,
            checkout_intent_id=90,
            user_pk=10,
            status="reconciled",
        )
        subscription = _sub(id=5, user_pk=10, status="active")
        intent = SimpleNamespace(
            id=90,
            status="ready_for_charge",
            intent_type="upgrade",
            target_plan_code="pro",
            addon_quantity=None,
            pricing_snapshot_json={},
            completed_at=None,
            updated_at=None,
        )
        current_plan_item = _item(item_type="plan_base", item_code="basic", effective_from=now)
        with (
            patch("services.billing.projector._lock_payment_attempt", return_value=attempt),
            patch("services.billing.projector._lock_subscription", return_value=subscription),
            patch("services.billing.projector._lock_checkout_intent", return_value=intent),
            patch("services.billing.projector._find_effective_subscription_item", return_value=current_plan_item),
            patch("services.billing.projector._create_plan_item") as create_item_mock,
            patch("services.billing.projector.db.session.add"),
            patch(
                "services.billing.projector.derive_projection_from_subscription",
                return_value=EntitlementProjection(plan_code="pro", plan_status="active", extra_account_slots=0),
            ),
            patch(
                "services.billing.projector._apply_projection_with_log",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ),
        ):
            apply_entitlement_from_payment_attempt(payment_attempt_id=31, source_id="reconcile-31")
        self.assertEqual(str(intent.status), "completed")
        self.assertIsNotNone(intent.completed_at)
        create_item_mock.assert_called_once()

    def test_apply_entitlement_from_payment_attempt_skips_completed_intent_mutation(self) -> None:
        attempt = SimpleNamespace(
            id=33,
            subscription_id=7,
            checkout_intent_id=91,
            user_pk=10,
            status="reconciled",
        )
        subscription = _sub(id=7, user_pk=10, status="active")
        intent = SimpleNamespace(
            id=91,
            status="completed",
            intent_type="addon_proration",
            target_plan_code="basic",
            addon_quantity=2,
            pricing_snapshot_json={"addon_quantity": 2},
            completed_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        with (
            patch("services.billing.projector._lock_payment_attempt", return_value=attempt),
            patch("services.billing.projector._lock_subscription", return_value=subscription),
            patch("services.billing.projector._lock_checkout_intent", return_value=intent),
            patch("services.billing.projector._apply_addon_quantity_for_intent") as addon_mock,
            patch("services.billing.projector.db.session.add"),
            patch(
                "services.billing.projector.derive_projection_from_subscription",
                return_value=EntitlementProjection(plan_code="basic", plan_status="active", extra_account_slots=2),
            ),
            patch(
                "services.billing.projector._apply_projection_with_log",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ),
        ):
            apply_entitlement_from_payment_attempt(payment_attempt_id=33, source_id="reconcile-33")
        addon_mock.assert_not_called()

    def test_apply_entitlement_from_subscription_state_uses_projection(self) -> None:
        subscription = _sub(id=5, user_pk=10, status="active")
        with (
            patch("services.billing.projector._lock_subscription", return_value=subscription),
            patch(
                "services.billing.projector.derive_projection_from_subscription",
                return_value=EntitlementProjection(plan_code="basic", plan_status="active", extra_account_slots=1),
            ),
            patch(
                "services.billing.projector._apply_projection_with_log",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ) as apply_mock,
        ):
            result = apply_entitlement_from_subscription_state(subscription_id=5, source_id="src-1")
        self.assertTrue(result["ok"])
        kwargs = apply_mock.call_args.kwargs
        self.assertEqual(kwargs["user_pk"], 10)
        self.assertEqual(kwargs["projection"], EntitlementProjection(plan_code="basic", plan_status="active", extra_account_slots=1))
        self.assertEqual(kwargs["source_id"], "src-1")

    def test_reproject_user_without_subscription_falls_back_to_free(self) -> None:
        with (
            patch("services.billing.projector.Subscription") as subscription_cls,
            patch(
                "services.billing.projector._apply_projection_with_log",
                return_value={"ok": True, "applied": True, "duplicate": False},
            ) as apply_mock,
        ):
            subscription_cls.query.filter.return_value.order_by.return_value.first.return_value = None
            result = reproject_entitlement_for_user(user_pk=77, source_id="manual-1")
        self.assertTrue(result["ok"])
        projection = apply_mock.call_args.kwargs["projection"]
        self.assertEqual(projection, EntitlementProjection(plan_code="free", plan_status="active", extra_account_slots=0))


if __name__ == "__main__":
    unittest.main()
