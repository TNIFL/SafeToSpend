from __future__ import annotations

import unittest
from sqlalchemy import CheckConstraint, UniqueConstraint

from domain.models import (
    BillingCustomer,
    BillingMethod,
    BillingMethodRegistrationAttempt,
    CheckoutIntent,
    EntitlementChangeLog,
    PaymentAttempt,
    PaymentEvent,
    Subscription,
    SubscriptionItem,
)


class BillingDomainModelsTest(unittest.TestCase):
    def test_expected_tables_are_registered(self) -> None:
        self.assertEqual(BillingCustomer.__tablename__, "billing_customers")
        self.assertEqual(BillingMethod.__tablename__, "billing_methods")
        self.assertEqual(BillingMethodRegistrationAttempt.__tablename__, "billing_method_registration_attempts")
        self.assertEqual(CheckoutIntent.__tablename__, "billing_checkout_intents")
        self.assertEqual(Subscription.__tablename__, "billing_subscriptions")
        self.assertEqual(SubscriptionItem.__tablename__, "billing_subscription_items")
        self.assertEqual(PaymentAttempt.__tablename__, "billing_payment_attempts")
        self.assertEqual(PaymentEvent.__tablename__, "billing_payment_events")
        self.assertEqual(EntitlementChangeLog.__tablename__, "entitlement_change_logs")

    def test_sensitive_card_fields_are_not_modeled(self) -> None:
        model_columns = {
            *BillingMethod.__table__.columns.keys(),
            *BillingMethodRegistrationAttempt.__table__.columns.keys(),
        }
        forbidden = {
            "card_number",
            "raw_card_number",
            "cvc",
            "raw_cvc",
            "card_password",
            "auth_key",
            "raw_auth_key",
            "expiration_month",
            "expiration_year",
        }
        self.assertTrue(forbidden.isdisjoint(model_columns))

    def test_unique_constraints_for_idempotency_are_present(self) -> None:
        payment_attempt_unique_names = {
            c.name
            for c in PaymentAttempt.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name
        }
        payment_event_unique_names = {
            c.name
            for c in PaymentEvent.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name
        }
        self.assertIn("uq_billing_payment_attempts_order_id", payment_attempt_unique_names)
        self.assertIn("uq_billing_payment_attempts_provider_payment_key", payment_attempt_unique_names)
        self.assertIn("uq_billing_payment_events_provider_hash", payment_event_unique_names)
        self.assertIn("uq_billing_payment_events_provider_tx", payment_event_unique_names)

    def test_subscription_status_constraint_includes_grace_started(self) -> None:
        checks = [
            c
            for c in Subscription.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_billing_subscriptions_status"
        ]
        self.assertEqual(len(checks), 1)
        sql = str(checks[0].sqltext)
        self.assertIn("grace_started", sql)

    def test_checkout_intent_status_constraint_includes_registration_and_charge_states(self) -> None:
        checks = [
            c
            for c in CheckoutIntent.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_billing_checkout_intents_status"
        ]
        self.assertEqual(len(checks), 1)
        sql = str(checks[0].sqltext)
        self.assertIn("registration_required", sql)
        self.assertIn("ready_for_charge", sql)
        self.assertIn("charge_started", sql)


if __name__ == "__main__":
    unittest.main()
