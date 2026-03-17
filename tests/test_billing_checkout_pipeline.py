from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from domain.models import PaymentAttempt
from services.billing.constants import (
    INTENT_TYPE_ADDON_PRORATION,
    INTENT_TYPE_INITIAL_SUBSCRIPTION,
    INTENT_TYPE_UPGRADE,
)
from services.billing.service import confirm_checkout_intent_charge


class _DummyCipher:
    def decrypt(self, _cipher_text: str) -> str:
        return "bk_live_dummy"


def _base_intent(intent_type: str, *, related_subscription_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=51,
        user_pk=7,
        intent_type=intent_type,
        target_plan_code=("basic" if intent_type == INTENT_TYPE_INITIAL_SUBSCRIPTION else "pro"),
        addon_quantity=(2 if intent_type == INTENT_TYPE_ADDON_PRORATION else None),
        amount_snapshot_krw=6900,
        currency="KRW",
        status="ready_for_charge",
        billing_method_id=None,
        related_subscription_id=related_subscription_id,
        pricing_snapshot_json={},
        expires_at=None,
        completed_at=None,
        updated_at=None,
    )


class BillingCheckoutPipelineTest(unittest.TestCase):
    def _run_confirm(self, intent: SimpleNamespace, *, expected_attempt_type: str, expected_subscription_id: int) -> None:
        method = SimpleNamespace(
            id=3,
            user_pk=7,
            status="active",
            billing_customer_id=12,
            encryption_key_version="v1",
            billing_key_enc="enc::dummy",
        )
        billing_customer = SimpleNamespace(id=12, user_pk=7, customer_key="cust_test_key")

        payload = {
            "user_pk": 7,
            "provider": "toss",
            "subscription_id": expected_subscription_id,
            "checkout_intent_id": int(intent.id),
            "attempt_type": expected_attempt_type,
            "order_id": "ord_checkout_pipeline_1",
            "amount_krw": int(intent.amount_snapshot_krw),
            "currency": "KRW",
            "status": "charge_started",
            "requested_at": None,
        }

        def _session_add_side_effect(row):
            if isinstance(row, PaymentAttempt) and not getattr(row, "id", None):
                row.id = 777

        with (
            patch("services.billing.service.lock_checkout_intent_by_resume_token", return_value=intent),
            patch("services.billing.service.resolve_checkout_billing_method", return_value=method),
            patch("services.billing.service._load_payment_attempt_by_intent", return_value=None),
            patch("services.billing.service.BillingCustomer") as customer_cls,
            patch(
                "services.billing.service._ensure_initial_subscription_context",
                return_value=expected_subscription_id,
            ) as ensure_initial_mock,
            patch("services.billing.service.build_payment_attempt_payload", return_value=payload),
            patch("services.billing.service.db.session.add", side_effect=_session_add_side_effect),
            patch("services.billing.service.db.session.commit"),
            patch(
                "services.billing.toss_client.build_billing_key_cipher_for_version",
                return_value=_DummyCipher(),
            ),
            patch(
                "services.billing.toss_client.charge_billing_key",
                return_value={
                    "provider_status": "done",
                    "order_id": "ord_checkout_pipeline_1",
                    "payment_key": "pay_checkout_pipeline_1",
                    "total_amount": int(intent.amount_snapshot_krw),
                    "currency": "KRW",
                },
            ),
            patch(
                "services.billing.reconcile.reconcile_by_order_id",
                return_value={
                    "ok": True,
                    "status_after": "reconciled",
                    "reconciled": True,
                    "reconcile_needed": False,
                },
            ) as reconcile_mock,
        ):
            customer_cls.query.filter_by.return_value.first.return_value = billing_customer
            result = confirm_checkout_intent_charge(
                user_pk=7,
                resume_token="ckt_resume_test_1",
                idempotency_key="idem_checkout_pipeline_1",
                commit=True,
            )

        if intent.intent_type == INTENT_TYPE_INITIAL_SUBSCRIPTION:
            ensure_initial_mock.assert_called_once()
        else:
            ensure_initial_mock.assert_not_called()
        reconcile_mock.assert_called_once()
        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("reconciled")))
        self.assertEqual(str(result.get("status_after")), "reconciled")
        self.assertEqual(str(intent.status), "charge_started")

    def test_initial_subscription_confirm_runs_charge_then_reconcile(self) -> None:
        intent = _base_intent(INTENT_TYPE_INITIAL_SUBSCRIPTION, related_subscription_id=None)
        self._run_confirm(intent, expected_attempt_type="initial", expected_subscription_id=81)

    def test_upgrade_confirm_runs_charge_then_reconcile(self) -> None:
        intent = _base_intent(INTENT_TYPE_UPGRADE, related_subscription_id=91)
        self._run_confirm(intent, expected_attempt_type="upgrade_full_charge", expected_subscription_id=91)

    def test_addon_proration_confirm_runs_charge_then_reconcile(self) -> None:
        intent = _base_intent(INTENT_TYPE_ADDON_PRORATION, related_subscription_id=101)
        self._run_confirm(intent, expected_attempt_type="addon_proration", expected_subscription_id=101)

    def test_confirm_is_noop_when_attempt_already_started_for_intent(self) -> None:
        intent = _base_intent(INTENT_TYPE_UPGRADE, related_subscription_id=91)
        method = SimpleNamespace(
            id=3,
            user_pk=7,
            status="active",
            billing_customer_id=12,
            encryption_key_version="v1",
            billing_key_enc="enc::dummy",
        )
        existing_attempt = SimpleNamespace(
            id=998,
            status="charge_started",
            order_id="ord_existing_started_1",
        )
        with (
            patch("services.billing.service.lock_checkout_intent_by_resume_token", return_value=intent),
            patch("services.billing.service.resolve_checkout_billing_method", return_value=method),
            patch("services.billing.service._load_payment_attempt_by_intent", return_value=existing_attempt),
            patch("services.billing.toss_client.charge_billing_key") as charge_mock,
            patch("services.billing.reconcile.reconcile_by_order_id") as reconcile_mock,
        ):
            result = confirm_checkout_intent_charge(
                user_pk=7,
                resume_token="ckt_resume_existing_1",
                idempotency_key="idem_existing_1",
                commit=True,
            )
        self.assertTrue(bool(result.get("ok")))
        self.assertTrue(bool(result.get("already_started")))
        self.assertEqual(str(result.get("order_id")), "ord_existing_started_1")
        charge_mock.assert_not_called()
        reconcile_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
