from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from itsdangerous import URLSafeSerializer

from routes.web.billing import web_billing_bp


class BillingRegisterCallbackRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(root / "templates"))
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(web_billing_bp)

        @app.get("/login", endpoint="web_auth.login")
        def _login():  # pragma: no cover - url_for target only
            return "login"

        @app.get("/pricing", endpoint="web_main.pricing")
        def _pricing():  # pragma: no cover - url_for target only
            return "pricing"

        @app.get("/", endpoint="web_main.landing")
        def _landing():  # pragma: no cover - url_for target only
            return "landing"

        @app.get("/preview", endpoint="web_main.preview")
        def _preview():  # pragma: no cover - url_for target only
            return "preview"

        @app.get("/bank", endpoint="web_bank.index")
        def _bank():  # pragma: no cover - url_for target only
            return "bank"

        self.app = app
        self.client = app.test_client()

    def _state(self, order_id: str, customer_key: str, resume: str | None = None) -> str:
        signer = URLSafeSerializer(self.app.config["SECRET_KEY"], salt="billing-register-callback-v1")
        payload = {"order_id": order_id, "customer_key": customer_key}
        if resume:
            payload["resume"] = resume
        return signer.dumps(payload)

    def test_success_callback_without_login_is_processed(self) -> None:
        state = self._state("reg_1", "cust_1")
        with patch(
            "routes.web.billing.complete_registration_success_by_order",
            return_value={"ok": True, "already_completed": False},
        ) as mocked, patch("routes.web.billing.build_billing_key_cipher_for_version", return_value=object()):
            resp = self.client.get(
                f"/dashboard/billing/register/success?attempt=reg_1&authKey=auth_abc&customerKey=cust_1&state={state}"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제수단 등록이 완료되었어요.", resp.get_data(as_text=True))
        mocked.assert_called_once()

    def test_success_callback_with_invalid_state_is_rejected(self) -> None:
        with patch("routes.web.billing.complete_registration_success_by_order") as mocked:
            resp = self.client.get(
                "/dashboard/billing/register/success?attempt=reg_1&authKey=auth_abc&customerKey=cust_1&state=bad"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제수단 등록을 완료하지 못했어요.", resp.get_data(as_text=True))
        mocked.assert_not_called()

    def test_fail_callback_without_login_records_failure(self) -> None:
        state = self._state("reg_2", "cust_2")
        with patch("routes.web.billing.mark_registration_failed_by_order", return_value=object()) as mocked:
            resp = self.client.get(
                f"/dashboard/billing/register/fail?attempt=reg_2&customerKey=cust_2&state={state}&code=FAILED"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("결제수단 등록을 완료하지 못했어요.", resp.get_data(as_text=True))
        mocked.assert_called_once()

    def test_success_callback_with_resume_redirects_processing_when_logged_in(self) -> None:
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
        resume_token = "ckt_resume_123"
        state = self._state("reg_3", "cust_3", resume=resume_token)
        resume_mock = patch(
            "routes.web.billing.resume_checkout_intent_after_registration",
            return_value={"ok": True, "resumed": True, "status": "ready_for_charge"},
        )
        with (
            patch(
                "routes.web.billing.complete_registration_success_by_order",
                return_value={"ok": True, "already_completed": False, "user_pk": 1, "billing_method_id": 10},
            ),
            resume_mock as resume_after_registration,
            patch("routes.web.billing.build_billing_key_cipher_for_version", return_value=object()),
        ):
            resp = self.client.get(
                f"/dashboard/billing/register/success?attempt=reg_3&authKey=auth_abc&customerKey=cust_3&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/billing/checkout/processing?intent=ckt_resume_123", resp.headers["Location"])
        resume_after_registration.assert_called_once()
        called_kwargs = resume_after_registration.call_args.kwargs
        self.assertEqual(int(called_kwargs["user_pk"]), 1)
        self.assertEqual(str(called_kwargs["resume_token"]), resume_token)
        self.assertEqual(int(called_kwargs["billing_method_id"]), 10)


if __name__ == "__main__":
    unittest.main()
