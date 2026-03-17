from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from flask import Flask

from routes.web.billing import web_billing_bp
from services.billing.service import BillingCheckoutValidationError


class BillingCheckoutRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(root / "templates"))
        app.config["SECRET_KEY"] = "checkout-routes-secret"
        app.register_blueprint(web_billing_bp)

        @app.get("/login", endpoint="web_auth.login")
        def _login():  # pragma: no cover
            return "login"

        @app.get("/pricing", endpoint="web_main.pricing")
        def _pricing():  # pragma: no cover
            return "pricing"

        @app.get("/", endpoint="web_main.landing")
        def _landing():  # pragma: no cover
            return "landing"

        @app.get("/preview", endpoint="web_main.preview")
        def _preview():  # pragma: no cover
            return "preview"

        @app.get("/overview", endpoint="web_main.overview")
        def _overview():  # pragma: no cover
            return "overview"

        @app.get("/dashboard/overview", endpoint="web_overview.overview")
        def _overview_dashboard():  # pragma: no cover
            return "overview-dashboard"

        @app.get("/dashboard/review", endpoint="web_calendar.review")
        def _review():  # pragma: no cover
            return "review"

        @app.get("/dashboard/tax-buffer", endpoint="web_calendar.tax_buffer")
        def _tax_buffer():  # pragma: no cover
            return "tax-buffer"

        @app.get("/dashboard/month", endpoint="web_calendar.month_calendar")
        def _month_calendar():  # pragma: no cover
            return "month-calendar"

        @app.get("/dashboard/reconcile", endpoint="web_calendar.reconcile")
        def _reconcile():  # pragma: no cover
            return "reconcile"

        @app.get("/dashboard/package", endpoint="web_package.page")
        def _package():  # pragma: no cover
            return "package"

        @app.get("/dashboard/vault", endpoint="web_vault.index")
        def _vault():  # pragma: no cover
            return "vault"

        @app.get("/dashboard/tax-profile", endpoint="web_profile.tax_profile")
        def _tax_profile():  # pragma: no cover
            return "tax-profile"

        @app.get("/dashboard/nhis", endpoint="web_profile.nhis_page")
        def _nhis_page():  # pragma: no cover
            return "nhis-page"

        @app.get("/support", endpoint="web_support.support_home")
        def _support_home():  # pragma: no cover
            return "support-home"

        @app.get("/admin", endpoint="web_admin.admin_index")
        def _admin_index():  # pragma: no cover
            return "admin-index"

        @app.get("/admin/ops", endpoint="web_admin.admin_ops")
        def _admin_ops():  # pragma: no cover
            return "admin-ops"

        @app.get("/admin/support", endpoint="web_admin.admin_support")
        def _admin_support():  # pragma: no cover
            return "admin-support"

        @app.get("/admin/assets", endpoint="web_profile.admin_assets_data")
        def _admin_assets_data():  # pragma: no cover
            return "admin-assets"

        @app.get("/admin/nhis-rates", endpoint="web_profile.admin_nhis_rates")
        def _admin_nhis_rates():  # pragma: no cover
            return "admin-nhis-rates"

        @app.get("/mypage", endpoint="web_profile.mypage")
        def _mypage():  # pragma: no cover
            return "mypage"

        @app.get("/bank", endpoint="web_bank.index")
        def _bank():  # pragma: no cover
            return "bank"

        @app.get("/inbox/import", endpoint="web_inbox.import_page")
        def _import_page():  # pragma: no cover
            return "import"

        @app.post("/logout", endpoint="web_auth.logout")
        def _logout():  # pragma: no cover
            return "logout"

        self.app = app
        self.client = app.test_client()

    def _login_session(self) -> None:
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1

    def test_checkout_confirm_submit_redirects_to_payment_success(self) -> None:
        self._login_session()
        with patch(
            "routes.web.billing.confirm_checkout_intent_charge",
            return_value={
                "ok": True,
                "intent_id": 1,
                "payment_attempt_id": 10,
                "order_id": "ord_test_1",
                "payment_key": "pay_test_1",
                "status_after": "reconciled",
                "reconciled": True,
            },
        ):
            resp = self.client.post(
                "/dashboard/billing/checkout/confirm",
                data={"intent": "ckt_token_1"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/billing/payment/success", resp.headers["Location"])
        self.assertIn("order_id=ord_test_1", resp.headers["Location"])

    def test_checkout_confirm_submit_validation_error_redirects_back(self) -> None:
        self._login_session()
        with patch(
            "routes.web.billing.confirm_checkout_intent_charge",
            side_effect=BillingCheckoutValidationError("지금은 결제를 진행할 수 없는 상태예요."),
        ):
            resp = self.client.post(
                "/dashboard/billing/checkout/confirm",
                data={"intent": "ckt_token_2"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/billing/checkout/confirm?intent=ckt_token_2", resp.headers["Location"])

    def test_checkout_start_requires_registration_launches_register_start_page(self) -> None:
        self._login_session()
        intent = SimpleNamespace(resume_token="ckt_resume_loop")
        with (
            patch(
                "routes.web.billing.start_checkout_intent",
                return_value={"intent": intent, "requires_registration": True},
            ),
            patch("routes.web.billing._render_registration_launch", return_value="register-launch-page"),
        ):
            resp = self.client.post(
                "/dashboard/billing/checkout/start",
                data={"operation_type": "initial_subscription", "target_plan": "basic"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("register-launch-page", resp.get_data(as_text=True))

    def test_register_start_renders_toss_launch_page_without_plan_gate(self) -> None:
        self._login_session()
        attempt = SimpleNamespace(id=1, order_id="reg_ord_1", customer_key="cust_1")
        payload = SimpleNamespace(
            provider="toss",
            client_key="test_ck_xxx",
            customer_key="cust_1",
            success_url="https://example.com/success",
            fail_url="https://example.com/fail",
        )
        with patch("routes.web.billing.start_registration_attempt", return_value=attempt), patch(
            "routes.web.billing.build_registration_payload",
            return_value=payload,
        ), patch(
            "routes.web.billing.render_template",
            return_value="billing-register-start:test_ck_xxx",
        ):
            resp = self.client.post(
                "/dashboard/billing/register/start",
                data={},
                follow_redirects=False,
            )
        text = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("billing-register-start", text)
        self.assertIn("test_ck_xxx", text)

    def test_checkout_processing_ready_for_charge_renders_processing_page(self) -> None:
        self._login_session()
        intent = SimpleNamespace(id=11, status="ready_for_charge", expires_at=None)
        with patch("routes.web.billing.get_checkout_intent_by_resume_token", return_value=intent):
            resp = self.client.get("/dashboard/billing/checkout/processing?intent=ckt_ready_1")
        text = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제를 연결하고 있어요.", text)
        self.assertIn("processing-confirm-form", text)

    def test_checkout_processing_charge_started_redirects_payment_success(self) -> None:
        self._login_session()
        intent = SimpleNamespace(id=12, status="charge_started", expires_at=None)
        attempt = SimpleNamespace(order_id="ord_proc_1", payment_key="pay_proc_1")
        payment_model = MagicMock()
        payment_model.query.filter_by.return_value.order_by.return_value.first.return_value = attempt
        with (
            patch("routes.web.billing.get_checkout_intent_by_resume_token", return_value=intent),
            patch("routes.web.billing.PaymentAttempt", payment_model),
        ):
            resp = self.client.get("/dashboard/billing/checkout/processing?intent=ckt_started_1", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/billing/payment/success", resp.headers["Location"])

    def test_checkout_confirm_page_renders_when_resolved_billing_method_exists(self) -> None:
        self._login_session()
        intent = SimpleNamespace(
            id=99,
            status="ready_for_charge",
            expires_at=None,
            intent_type="initial_subscription",
            target_plan_code="basic",
            addon_quantity=None,
            amount_snapshot_krw=6900,
            billing_method_id=123,
        )
        active_method = SimpleNamespace(id=123, status="active")
        with (
            patch("routes.web.billing.get_checkout_intent_by_resume_token", return_value=intent),
            patch("routes.web.billing.resolve_checkout_billing_method", return_value=active_method),
        ):
            resp = self.client.get("/dashboard/billing/checkout/confirm?intent=ckt_ready_99", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제 내용 확인", resp.get_data(as_text=True))

    def test_payment_success_reconciled_state(self) -> None:
        attempt = SimpleNamespace(
            order_id="ord_1",
            payment_key="pay_1",
            status="reconciled",
            attempt_type="upgrade_full_charge",
            fail_message_norm=None,
            checkout_intent_id=None,
        )
        with patch("routes.web.billing._load_payment_attempt_for_result", return_value=attempt):
            resp = self.client.get("/dashboard/billing/payment/success?order_id=ord_1")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/pricing", resp.headers["Location"])

    def test_payment_success_pending_state(self) -> None:
        attempt = SimpleNamespace(
            order_id="ord_2",
            payment_key="pay_2",
            status="authorized",
            attempt_type="initial",
            fail_message_norm=None,
        )
        with patch("routes.web.billing._load_payment_attempt_for_result", return_value=attempt):
            resp = self.client.get("/dashboard/billing/payment/success?order_id=ord_2")
        text = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제를 확인하고 있어요.", text)
        self.assertIn("상태 새로고침", text)

    def test_payment_fail_uses_attempt_fail_message(self) -> None:
        attempt = SimpleNamespace(
            order_id="ord_3",
            payment_key="pay_3",
            status="failed",
            attempt_type="addon_proration",
            fail_code="PAYMENT_FAIL",
            fail_message_norm="카드 한도 초과",
            checkout_intent_id=None,
        )
        with patch("routes.web.billing._load_payment_attempt_for_result", return_value=attempt):
            resp = self.client.get("/dashboard/billing/payment/fail?order_id=ord_3")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/pricing", resp.headers["Location"])

    def test_payment_success_redirects_to_sanitized_return_to(self) -> None:
        attempt = SimpleNamespace(
            order_id="ord_ret_1",
            payment_key="pay_ret_1",
            status="reconciled",
            attempt_type="initial",
            fail_message_norm=None,
            checkout_intent_id=77,
        )
        intent = SimpleNamespace(pricing_snapshot_json={"return_to": "/dashboard/package?month=2026-03"})
        with (
            patch("routes.web.billing._load_payment_attempt_for_result", return_value=attempt),
            patch("routes.web.billing.get_checkout_intent", return_value=intent),
        ):
            resp = self.client.get("/dashboard/billing/payment/success?order_id=ord_ret_1", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/package?month=2026-03", resp.headers["Location"])

    def test_payment_success_blocks_external_return_to(self) -> None:
        attempt = SimpleNamespace(
            order_id="ord_ret_2",
            payment_key="pay_ret_2",
            status="reconciled",
            attempt_type="initial",
            fail_message_norm=None,
            checkout_intent_id=78,
        )
        intent = SimpleNamespace(pricing_snapshot_json={"return_to": "https://evil.example/phish"})
        with (
            patch("routes.web.billing._load_payment_attempt_for_result", return_value=attempt),
            patch("routes.web.billing.get_checkout_intent", return_value=intent),
        ):
            resp = self.client.get("/dashboard/billing/payment/success?order_id=ord_ret_2", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/pricing", resp.headers["Location"])


if __name__ == "__main__":
    unittest.main()
