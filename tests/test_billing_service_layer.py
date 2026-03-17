from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy.exc import IntegrityError

from core.extensions import db
from domain.models import BillingMethod
from services.billing.constants import (
    ADDON_ACCOUNT_SLOT_PRICE_KRW,
    ATTEMPT_TYPE_ADDON_PRORATION,
    ATTEMPT_TYPE_INITIAL,
    BASIC_PRICE_KRW,
    CHECKOUT_INTENT_STATUS_CREATED,
    CHECKOUT_INTENT_STATUS_READY_FOR_CHARGE,
    INTENT_TYPE_INITIAL_SUBSCRIPTION,
    INTENT_TYPE_ADDON_PRORATION,
    INTENT_TYPE_UPGRADE,
    GRACE_DAYS,
    PLAN_BASIC,
    PLAN_FREE,
    PLAN_PRO,
    PRO_PRICE_KRW,
    PROVIDER_TOSS,
)
from services.billing.idempotency import (
    build_event_hash,
    build_idempotency_token,
    is_duplicate_by_keys,
    normalize_order_id,
)
from services.billing.pricing import (
    calculate_addon_proration,
    derive_next_billing_amount,
    is_subscription_in_grace,
    should_transition_to_past_due,
)
from services.billing.security import (
    BillingSecurityError,
    encrypt_billing_key,
    ensure_auth_key_not_persisted,
    hash_billing_key,
    normalize_fail_message,
)
from services.billing.service import (
    _complete_registration_success_for_attempt,
    BillingCheckoutValidationError,
    BillingDomainError,
    build_checkout_intent_draft,
    build_payment_attempt_draft,
    build_payment_attempt_payload,
    build_registration_attempt_draft,
    create_checkout_intent,
    generate_customer_key,
    generate_order_id,
    get_active_billing_method,
    get_intent_bound_billing_method,
    get_checkout_intent_by_resume_token,
    get_or_create_billing_customer,
    resolve_checkout_billing_method,
    resolve_checkout_pricing,
    resume_checkout_intent_after_registration,
    start_checkout_intent,
    transition_checkout_intent_status,
)
from services.billing.state_machine import (
    EVENT_PAYMARK_AUTHORIZED,
    EVENT_PAYMARK_RECONCILE_NEEDED,
    EVENT_PAYMARK_RECONCILED,
    EVENT_SUB_ACTIVATE,
    EVENT_SUB_MARK_PAST_DUE,
    EVENT_SUB_RECOVER_PAYMENT,
    EVENT_SUB_REQUEST_CANCEL,
    EVENT_SUB_START_GRACE,
    StateTransitionError,
    transition_payment_attempt_state,
    transition_subscription_state,
)


class _DummyCipher:
    def encrypt(self, plain_text: str) -> str:
        return f"enc::{plain_text}"

    def decrypt(self, cipher_text: str) -> str:
        return cipher_text.replace("enc::", "", 1)


class BillingServiceLayerTest(unittest.TestCase):
    def test_constants_policy_values(self) -> None:
        self.assertEqual(BASIC_PRICE_KRW, 6900)
        self.assertEqual(PRO_PRICE_KRW, 12900)
        self.assertEqual(ADDON_ACCOUNT_SLOT_PRICE_KRW, 3000)
        self.assertEqual(GRACE_DAYS, 3)
        self.assertEqual(PLAN_FREE, "free")
        self.assertEqual(PLAN_BASIC, "basic")
        self.assertEqual(PLAN_PRO, "pro")
        self.assertEqual(PROVIDER_TOSS, "toss")

    def test_generate_customer_key_and_order_id(self) -> None:
        customer_key = generate_customer_key()
        order_id = generate_order_id()
        self.assertTrue(customer_key.startswith("cust_"))
        self.assertTrue(order_id.startswith("ord_"))
        self.assertNotEqual(customer_key, generate_customer_key())
        self.assertNotEqual(order_id, generate_order_id())

    def test_registration_attempt_draft(self) -> None:
        draft = build_registration_attempt_draft(1)
        self.assertEqual(draft["user_pk"], 1)
        self.assertEqual(draft["provider"], PROVIDER_TOSS)
        self.assertEqual(draft["status"], "registration_started")
        self.assertTrue(draft["order_id"].startswith("reg_"))

    def test_payment_attempt_draft_and_payload(self) -> None:
        draft = build_payment_attempt_draft(
            user_pk=1,
            attempt_type=ATTEMPT_TYPE_INITIAL,
            amount_krw=6900,
        )
        self.assertEqual(draft.operation_type, ATTEMPT_TYPE_INITIAL)
        self.assertEqual(draft.amount_krw, 6900)
        payload = build_payment_attempt_payload(
            user_pk=1,
            attempt_type=ATTEMPT_TYPE_ADDON_PRORATION,
            amount_krw=1200,
            subscription_id=9,
        )
        self.assertEqual(payload["status"], "charge_started")
        self.assertEqual(payload["subscription_id"], 9)

    def test_payment_attempt_draft_rejects_invalid_type(self) -> None:
        with self.assertRaises(BillingDomainError):
            build_payment_attempt_draft(user_pk=1, attempt_type="invalid", amount_krw=1000)

    def test_subscription_state_machine(self) -> None:
        status = transition_subscription_state("pending_activation", EVENT_SUB_ACTIVATE)
        self.assertEqual(status, "active")
        status = transition_subscription_state(status, EVENT_SUB_START_GRACE)
        self.assertEqual(status, "grace_started")
        status = transition_subscription_state(status, EVENT_SUB_RECOVER_PAYMENT)
        self.assertEqual(status, "active")
        status = transition_subscription_state(status, EVENT_SUB_REQUEST_CANCEL)
        self.assertEqual(status, "cancel_requested")
        with self.assertRaises(StateTransitionError):
            transition_subscription_state("cancel_requested", EVENT_SUB_MARK_PAST_DUE)

    def test_payment_attempt_state_machine(self) -> None:
        status = transition_payment_attempt_state("charge_started", EVENT_PAYMARK_AUTHORIZED)
        self.assertEqual(status, "authorized")
        status = transition_payment_attempt_state(status, EVENT_PAYMARK_RECONCILE_NEEDED)
        self.assertEqual(status, "reconcile_needed")
        status = transition_payment_attempt_state(status, EVENT_PAYMARK_RECONCILED)
        self.assertEqual(status, "reconciled")

    def test_addon_proration(self) -> None:
        anchor = datetime(2026, 3, 1, tzinfo=timezone.utc)
        period_end = datetime(2026, 4, 1, tzinfo=timezone.utc)
        full = calculate_addon_proration(
            anchor=anchor,
            current_period_end=period_end,
            quantity=1,
            unit_price_krw=3000,
            as_of=anchor,
        )
        self.assertEqual(full, 3000)
        halfish = calculate_addon_proration(
            anchor=anchor,
            current_period_end=period_end,
            quantity=1,
            unit_price_krw=3000,
            as_of=datetime(2026, 3, 16, tzinfo=timezone.utc),
        )
        self.assertTrue(1200 <= halfish <= 1800)

    def test_derive_next_billing_amount(self) -> None:
        now = datetime(2026, 3, 20, tzinfo=timezone.utc)
        items = [
            {
                "status": "active",
                "amount_krw": 6900,
                "effective_from": datetime(2026, 3, 1, tzinfo=timezone.utc),
                "effective_to": None,
            },
            {
                "status": "active",
                "quantity": 2,
                "unit_price_krw": 3000,
                "effective_from": datetime(2026, 3, 10, tzinfo=timezone.utc),
                "effective_to": None,
            },
            {
                "status": "removed",
                "amount_krw": 99999,
                "effective_from": datetime(2026, 3, 1, tzinfo=timezone.utc),
                "effective_to": None,
            },
        ]
        total = derive_next_billing_amount({}, items, at=now)
        self.assertEqual(total, 12900)

    def test_grace_helpers(self) -> None:
        sub = {"status": "grace_started", "grace_until": datetime(2026, 3, 20, tzinfo=timezone.utc)}
        self.assertTrue(is_subscription_in_grace(sub, now=datetime(2026, 3, 19, tzinfo=timezone.utc)))
        self.assertTrue(should_transition_to_past_due(sub, now=datetime(2026, 3, 20, tzinfo=timezone.utc)))

    def test_security_helpers(self) -> None:
        ensure_auth_key_not_persisted({"foo": "bar"})
        with self.assertRaises(BillingSecurityError):
            ensure_auth_key_not_persisted({"authKey": "sensitive"})
        digest = hash_billing_key("billing-key-value")
        self.assertEqual(len(digest), 64)
        with self.assertRaises(BillingSecurityError):
            encrypt_billing_key("billing-key-value", cipher=None)
        encrypted = encrypt_billing_key("billing-key-value", cipher=_DummyCipher())
        self.assertTrue(encrypted.startswith("enc::"))
        self.assertIn("****3456", normalize_fail_message("계좌 1234-56-789012-3456 오류"))

    def test_idempotency_helpers(self) -> None:
        self.assertEqual(normalize_order_id("ord_ABC-123"), "ord_ABC-123")
        self.assertIsNone(normalize_order_id("bad id with space"))
        h1 = build_event_hash({"a": 1, "b": 2})
        h2 = build_event_hash({"b": 2, "a": 1})
        self.assertEqual(h1, h2)
        kind, token = build_idempotency_token(order_id="ord_1", payment_key=None, transmission_id=None)
        self.assertEqual(kind, "order_id")
        self.assertEqual(token, "ord_1")
        self.assertTrue(
            is_duplicate_by_keys(
                existing_order_ids={"ord_1"},
                order_id="ord_1",
                payment_key=None,
                transmission_id=None,
            )
        )

    def test_get_or_create_billing_customer_recovers_on_integrity_error(self) -> None:
        existing = MagicMock(id=99, user_pk=1, provider="toss")
        with (
            patch("services.billing.service.BillingCustomer") as customer_cls,
            patch("services.billing.service.db.session.add"),
            patch(
                "services.billing.service.db.session.flush",
                side_effect=IntegrityError("insert", {}, Exception("dup")),
            ),
            patch("services.billing.service.db.session.rollback") as rollback_mock,
        ):
            customer_cls.query.filter_by.return_value.first.side_effect = [None, existing]
            row = get_or_create_billing_customer(user_pk=1, provider="toss")
        self.assertIs(row, existing)
        rollback_mock.assert_called_once()

    def test_get_active_billing_method_falls_back_without_provider_filter(self) -> None:
        provider_scoped_query = MagicMock()
        provider_scoped_query.filter.return_value = provider_scoped_query
        provider_scoped_query.order_by.return_value.first.return_value = None

        fallback_query = MagicMock()
        fallback_query.filter.return_value = fallback_query
        legacy_method = SimpleNamespace(id=501, user_pk=7, provider="tosspayments", status="active")
        fallback_query.order_by.return_value.first.return_value = legacy_method

        query_root = MagicMock()
        query_root.filter.side_effect = [provider_scoped_query, fallback_query]

        with patch("services.billing.service.BillingMethod") as billing_method_cls:
            billing_method_cls.query = query_root
            row = get_active_billing_method(user_pk=7, provider="toss")

        self.assertIs(row, legacy_method)
        self.assertEqual(query_root.filter.call_count, 2)

    def test_checkout_intent_draft_creation(self) -> None:
        draft = build_checkout_intent_draft(
            user_pk=1,
            intent_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
            target_plan_code="basic",
            amount_snapshot_krw=6900,
            pricing_snapshot_json={"amount_snapshot_krw": 6900},
        )
        self.assertEqual(draft.user_pk, 1)
        self.assertEqual(draft.intent_type, INTENT_TYPE_INITIAL_SUBSCRIPTION)
        self.assertEqual(draft.status, CHECKOUT_INTENT_STATUS_CREATED)
        self.assertTrue(str(draft.resume_token).startswith("ckt_"))
        self.assertGreaterEqual(int(draft.amount_snapshot_krw), 0)

    def test_checkout_intent_status_transition(self) -> None:
        next_status = transition_checkout_intent_status(
            CHECKOUT_INTENT_STATUS_CREATED,
            CHECKOUT_INTENT_STATUS_READY_FOR_CHARGE,
        )
        self.assertEqual(next_status, CHECKOUT_INTENT_STATUS_READY_FOR_CHARGE)
        with self.assertRaises(BillingDomainError):
            transition_checkout_intent_status("completed", CHECKOUT_INTENT_STATUS_READY_FOR_CHARGE)

    def test_create_checkout_intent_idempotency_fallback(self) -> None:
        existing = MagicMock(id=5, user_pk=1, idempotency_key="idemp-1")
        with (
            patch("services.billing.service.db.session.add"),
            patch(
                "services.billing.service.db.session.commit",
                side_effect=IntegrityError("insert", {}, Exception("dup")),
            ),
            patch("services.billing.service.db.session.rollback"),
            patch("services.billing.service._find_checkout_intent_by_user_idempotency", return_value=existing),
            patch("services.billing.service._find_checkout_intent_by_resume_token", return_value=None),
        ):
            row, created = create_checkout_intent(
                user_pk=1,
                intent_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
                amount_snapshot_krw=6900,
                pricing_snapshot_json={"amount_snapshot_krw": 6900},
                idempotency_key="idemp-1",
            )
        self.assertFalse(created)
        self.assertIs(row, existing)

    def test_get_checkout_intent_by_resume_token_invalid(self) -> None:
        self.assertIsNone(get_checkout_intent_by_resume_token(""))

    def test_resolve_checkout_pricing_initial_subscription(self) -> None:
        with (
            patch(
                "services.billing.service.get_user_entitlements",
                return_value=SimpleNamespace(plan_code="free", plan_status="active"),
            ),
            patch("services.billing.service._latest_active_subscription_for_user", return_value=None),
        ):
            pricing = resolve_checkout_pricing(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
            )
        self.assertEqual(pricing["amount_krw"], 6900)
        self.assertEqual(pricing["target_plan_code"], "basic")
        self.assertEqual(pricing["operation_type"], INTENT_TYPE_INITIAL_SUBSCRIPTION)

    def test_resolve_checkout_pricing_upgrade(self) -> None:
        sub = SimpleNamespace(
            id=12,
            status="active",
            billing_anchor_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        with (
            patch(
                "services.billing.service.get_user_entitlements",
                return_value=SimpleNamespace(plan_code="basic", plan_status="active"),
            ),
            patch("services.billing.service._latest_active_subscription_for_user", return_value=sub),
        ):
            pricing = resolve_checkout_pricing(
                user_pk=1,
                operation_type=INTENT_TYPE_UPGRADE,
                target_plan_code="pro",
            )
        self.assertEqual(pricing["amount_krw"], 12900)
        self.assertEqual(pricing["related_subscription_id"], 12)
        self.assertEqual(pricing["target_plan_code"], "pro")

    def test_resolve_checkout_pricing_addon_rejects_non_positive_qty(self) -> None:
        sub = SimpleNamespace(
            id=12,
            status="active",
            billing_anchor_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            current_period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        with (
            patch(
                "services.billing.service.get_user_entitlements",
                return_value=SimpleNamespace(plan_code="basic", plan_status="active"),
            ),
            patch("services.billing.service._latest_active_subscription_for_user", return_value=sub),
        ):
            with self.assertRaises(BillingCheckoutValidationError):
                resolve_checkout_pricing(
                    user_pk=1,
                    operation_type=INTENT_TYPE_ADDON_PRORATION,
                    addon_quantity=0,
                )

    def test_resolve_checkout_pricing_blocks_addon_for_free(self) -> None:
        with (
            patch(
                "services.billing.service.get_user_entitlements",
                return_value=SimpleNamespace(plan_code="free", plan_status="active"),
            ),
            patch("services.billing.service._latest_active_subscription_for_user", return_value=None),
        ):
            with self.assertRaises(BillingCheckoutValidationError):
                resolve_checkout_pricing(
                    user_pk=1,
                    operation_type=INTENT_TYPE_ADDON_PRORATION,
                    addon_quantity=1,
                )

    def test_start_checkout_intent_reuses_open_intent(self) -> None:
        existing = SimpleNamespace(
            id=91,
            status="registration_required",
            billing_method_id=None,
            pricing_snapshot_json={"foo": "bar"},
            amount_snapshot_krw=6900,
            currency="KRW",
        )
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=existing),
            patch("services.billing.service.get_active_billing_method", return_value=None),
            patch("services.billing.service.db.session.add"),
            patch("services.billing.service.db.session.commit"),
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
            )
        self.assertFalse(result["created"])
        self.assertEqual(result["next_step"], "registration")
        self.assertEqual(result["amount_krw"], 6900)

    def test_start_checkout_intent_creates_ready_for_charge_when_method_exists(self) -> None:
        intent_obj = SimpleNamespace(id=77, status="ready_for_charge")
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=None),
            patch("services.billing.service.get_active_billing_method", return_value=SimpleNamespace(id=3)),
            patch(
                "services.billing.service.resolve_checkout_pricing",
                return_value={
                    "operation_type": INTENT_TYPE_INITIAL_SUBSCRIPTION,
                    "target_plan_code": "basic",
                    "addon_quantity": None,
                    "amount_krw": 6900,
                    "currency": "KRW",
                    "pricing_snapshot_json": {"operation_type": INTENT_TYPE_INITIAL_SUBSCRIPTION},
                    "related_subscription_id": None,
                    "requires_billing_method": True,
                },
            ),
            patch("services.billing.service.create_checkout_intent", return_value=(intent_obj, True)) as create_mock,
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
                return_to="/dashboard/package?month=2026-03",
            )
        self.assertTrue(result["created"])
        self.assertEqual(result["next_step"], "confirm")
        self.assertEqual(result["intent"], intent_obj)
        self.assertEqual(create_mock.call_args.kwargs["status"], "ready_for_charge")
        self.assertEqual(create_mock.call_args.kwargs["billing_method_id"], 3)
        self.assertEqual(
            create_mock.call_args.kwargs["pricing_snapshot_json"].get("return_to"),
            "/dashboard/package?month=2026-03",
        )

    def test_start_checkout_intent_updates_return_to_on_reused_intent(self) -> None:
        existing = SimpleNamespace(
            id=88,
            status="registration_required",
            billing_method_id=None,
            pricing_snapshot_json={"amount_snapshot_krw": 6900},
            amount_snapshot_krw=6900,
            currency="KRW",
            updated_at=None,
        )
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=existing),
            patch("services.billing.service.get_active_billing_method", return_value=None),
            patch("services.billing.service.db.session.add"),
            patch("services.billing.service.db.session.commit"),
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
                return_to="/dashboard/bank?tab=plans",
            )
        self.assertFalse(result["created"])
        self.assertEqual(existing.pricing_snapshot_json.get("return_to"), "/dashboard/bank?tab=plans")

    def test_start_checkout_intent_ready_for_charge_not_demoted_when_bound_method_exists(self) -> None:
        existing = SimpleNamespace(
            id=101,
            status="ready_for_charge",
            billing_method_id=41,
            pricing_snapshot_json={"amount_snapshot_krw": 6900},
            amount_snapshot_krw=6900,
            currency="KRW",
            updated_at=None,
        )
        bound_method = SimpleNamespace(id=41, user_pk=1, status="active")
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=existing),
            patch("services.billing.service.get_active_billing_method", return_value=None),
            patch("services.billing.service.get_intent_bound_billing_method", return_value=bound_method),
            patch("services.billing.service.db.session.add"),
            patch("services.billing.service.db.session.commit"),
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
            )
        self.assertEqual(existing.status, "ready_for_charge")
        self.assertEqual(result["next_step"], "confirm")
        self.assertFalse(result["requires_registration"])

    def test_start_checkout_intent_demotes_when_bound_method_invalid(self) -> None:
        existing = SimpleNamespace(
            id=102,
            status="ready_for_charge",
            billing_method_id=42,
            pricing_snapshot_json={"amount_snapshot_krw": 6900},
            amount_snapshot_krw=6900,
            currency="KRW",
            updated_at=None,
        )
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=existing),
            patch("services.billing.service.get_active_billing_method", return_value=None),
            patch("services.billing.service.get_intent_bound_billing_method", return_value=None),
            patch("services.billing.service.db.session.add"),
            patch("services.billing.service.db.session.commit"),
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
            )
        self.assertEqual(existing.status, "registration_required")
        self.assertEqual(result["next_step"], "registration")
        self.assertTrue(result["requires_registration"])

    def test_start_checkout_intent_rebinds_ready_intent_to_active_method_when_bound_is_invalid(self) -> None:
        existing = SimpleNamespace(
            id=103,
            status="ready_for_charge",
            billing_method_id=42,
            pricing_snapshot_json={"amount_snapshot_krw": 6900},
            amount_snapshot_krw=6900,
            currency="KRW",
            updated_at=None,
        )
        active_method = SimpleNamespace(id=88, status="active")
        with (
            patch("services.billing.service.find_reusable_checkout_intent", return_value=existing),
            patch("services.billing.service.get_active_billing_method", return_value=active_method),
            patch("services.billing.service.get_intent_bound_billing_method", return_value=None),
            patch("services.billing.service.db.session.add"),
            patch("services.billing.service.db.session.commit"),
        ):
            result = start_checkout_intent(
                user_pk=1,
                operation_type=INTENT_TYPE_INITIAL_SUBSCRIPTION,
                target_plan_code="basic",
            )
        self.assertEqual(existing.status, "ready_for_charge")
        self.assertEqual(int(existing.billing_method_id), 88)
        self.assertEqual(result["next_step"], "confirm")
        self.assertFalse(result["requires_registration"])

    def test_get_intent_bound_billing_method_blocks_other_user_method(self) -> None:
        intent = SimpleNamespace(billing_method_id=123)
        with patch("services.billing.service.BillingMethod") as billing_method_cls:
            billing_method_cls.query.filter_by.return_value.first.return_value = None
            row = get_intent_bound_billing_method(user_pk=7, intent=intent)
        self.assertIsNone(row)
        billing_method_cls.query.filter_by.assert_called_once_with(id=123, user_pk=7)

    def test_get_intent_bound_billing_method_blocks_inactive_method(self) -> None:
        intent = SimpleNamespace(billing_method_id=124)
        inactive_row = SimpleNamespace(id=124, user_pk=7, status="revoked")
        with patch("services.billing.service.BillingMethod") as billing_method_cls:
            billing_method_cls.query.filter_by.return_value.first.return_value = inactive_row
            row = get_intent_bound_billing_method(user_pk=7, intent=intent)
        self.assertIsNone(row)

    def test_resolve_checkout_billing_method_prefers_intent_bound_method(self) -> None:
        intent = SimpleNamespace(billing_method_id=500)
        bound = SimpleNamespace(id=500, user_pk=7, status="active")
        with (
            patch("services.billing.service.get_intent_bound_billing_method", return_value=bound) as bound_mock,
            patch("services.billing.service.get_active_billing_method") as fallback_mock,
        ):
            resolved = resolve_checkout_billing_method(user_pk=7, intent=intent)
        self.assertIs(resolved, bound)
        bound_mock.assert_called_once()
        fallback_mock.assert_not_called()

    def test_resolve_checkout_billing_method_falls_back_when_bound_id_invalid(self) -> None:
        intent = SimpleNamespace(billing_method_id=700)
        fallback = SimpleNamespace(id=701, user_pk=7, status="active")
        with (
            patch("services.billing.service.get_intent_bound_billing_method", return_value=None),
            patch("services.billing.service.get_active_billing_method", return_value=fallback) as fallback_mock,
        ):
            resolved = resolve_checkout_billing_method(user_pk=7, intent=intent)
        self.assertIs(resolved, fallback)
        fallback_mock.assert_called_once_with(user_pk=7)

    def test_resolve_checkout_billing_method_returns_none_when_no_bound_and_no_active(self) -> None:
        intent = SimpleNamespace(billing_method_id=700)
        with (
            patch("services.billing.service.get_intent_bound_billing_method", return_value=None),
            patch("services.billing.service.get_active_billing_method", return_value=None),
        ):
            resolved = resolve_checkout_billing_method(user_pk=7, intent=intent)
        self.assertIsNone(resolved)

    def test_registration_success_keeps_new_method_active_when_previous_exists(self) -> None:
        attempt = SimpleNamespace(
            id=41,
            user_pk=7,
            provider="toss",
            order_id="reg_case_keep_active",
            customer_key="cust_case_keep_active",
            status="registration_started",
            completed_at=None,
            updated_at=None,
            fail_code=None,
            fail_message_norm=None,
        )
        customer = SimpleNamespace(id=90)
        old_method = SimpleNamespace(id=301, status="active", updated_at=None)
        added_rows: list = []

        class _PrevMethodQuery:
            def __init__(self) -> None:
                self._filters: list = []

            def filter(self, expression):
                self._filters.append(expression)
                return self

            def all(self):
                id_filter = None
                for expr in self._filters:
                    left_obj = getattr(expr, "left", None)
                    left = str(left_obj) if left_obj is not None else ""
                    if left.endswith(".id"):
                        id_filter = expr
                        break
                id_value = getattr(getattr(id_filter, "right", None), "value", None)
                if id_value is None:
                    created = next(
                        (
                            row
                            for row in added_rows
                            if isinstance(row, BillingMethod) and int(getattr(row, "id", 0) or 0) > 0
                        ),
                        None,
                    )
                    if created is not None:
                        return [created, old_method]
                return [old_method]

        method_query = MagicMock()
        method_query.filter_by.return_value.first.return_value = None
        method_query.filter.return_value = _PrevMethodQuery()

        def _session_add_side_effect(row):
            added_rows.append(row)

        def _session_flush_side_effect():
            for row in added_rows:
                if isinstance(row, BillingMethod) and int(getattr(row, "id", 0) or 0) <= 0:
                    row.id = 999

        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        with app.app_context():
            with (
                patch("services.billing.service.get_or_create_billing_customer", return_value=customer),
                patch.object(BillingMethod, "query", method_query),
                patch("services.billing.service.db.session.add", side_effect=_session_add_side_effect),
                patch("services.billing.service.db.session.flush", side_effect=_session_flush_side_effect) as flush_mock,
                patch("services.billing.service.db.session.commit"),
            ):
                result = _complete_registration_success_for_attempt(
                    attempt=attempt,
                    auth_key="auth-ok-1",
                    customer_key="cust_case_keep_active",
                    exchange_auth_key_fn=lambda **_kwargs: {"billing_key": "bk_keep_active_1"},
                    key_cipher=_DummyCipher(),
                    encryption_key_version="v1",
                )

        created = next(
            (
                row
                for row in added_rows
                if isinstance(row, BillingMethod) and int(getattr(row, "id", 0) or 0) == 999
            ),
            None,
        )
        self.assertTrue(bool(result.get("ok")))
        self.assertIsNotNone(created)
        self.assertEqual(str(getattr(created, "status", "")), "active")
        self.assertEqual(str(getattr(old_method, "status", "")), "inactive")
        flush_mock.assert_called()

    def test_resume_checkout_intent_after_registration_is_idempotent_for_ready_status(self) -> None:
        intent = SimpleNamespace(
            id=301,
            status="ready_for_charge",
            billing_method_id=17,
            expires_at=None,
            updated_at=None,
        )
        with patch("services.billing.service.get_checkout_intent_by_resume_token", return_value=intent):
            result = resume_checkout_intent_after_registration(
                user_pk=7,
                resume_token="ckt_resume_301",
                billing_method_id=17,
                commit=False,
            )
        self.assertTrue(bool(result.get("ok")))
        self.assertFalse(bool(result.get("resumed")))
        self.assertEqual(str(result.get("reason")), "already_ready_for_charge")


if __name__ == "__main__":
    unittest.main()
