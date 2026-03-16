from __future__ import annotations

import unittest
from unittest.mock import patch

from services.billing.service import (
    reconcile_payment_by_order_id,
    reconcile_payment_by_payment_key,
    reconcile_payment_from_event,
)


class BillingReconcileWrapperTest(unittest.TestCase):
    def test_reconcile_by_order_wrapper(self) -> None:
        with patch(
            "services.billing.reconcile.reconcile_by_order_id",
            return_value={"status_after": "reconciled"},
        ) as mocked:
            out = reconcile_payment_by_order_id(order_id="ord_1", apply_projection=True)
        self.assertEqual(out["status_after"], "reconciled")
        mocked.assert_called_once_with(order_id="ord_1", apply_projection=True, commit=True)

    def test_reconcile_by_payment_key_wrapper(self) -> None:
        with patch(
            "services.billing.reconcile.reconcile_by_payment_key",
            return_value={"status_after": "reconcile_needed"},
        ) as mocked:
            out = reconcile_payment_by_payment_key(payment_key="pay_1", apply_projection=False)
        self.assertEqual(out["status_after"], "reconcile_needed")
        mocked.assert_called_once_with(payment_key="pay_1", apply_projection=False, commit=True)

    def test_reconcile_from_event_wrapper(self) -> None:
        with patch(
            "services.billing.reconcile.reconcile_from_payment_event",
            return_value={"ok": True, "payment_event_id": 9},
        ) as mocked:
            out = reconcile_payment_from_event(payment_event_id=9, apply_projection=True)
        self.assertTrue(out["ok"])
        mocked.assert_called_once_with(
            payment_event_id=9,
            transmission_id=None,
            apply_projection=True,
            commit=True,
        )


if __name__ == "__main__":
    unittest.main()

