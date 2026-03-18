from __future__ import annotations

import unittest
from pathlib import Path

from flask import Blueprint, Flask

from routes.web.billing import web_billing_bp


class BillingRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        template_folder = str(Path(__file__).resolve().parents[1] / "templates")
        app = Flask(__name__, template_folder=template_folder)
        app.secret_key = "test-secret"

        main_bp = Blueprint("web_main", __name__)
        overview_bp = Blueprint("web_overview", __name__)
        bank_bp = Blueprint("web_bank", __name__)
        inbox_bp = Blueprint("web_inbox", __name__)
        auth_bp = Blueprint("web_auth", __name__)
        calendar_bp = Blueprint("web_calendar", __name__)
        vault_bp = Blueprint("web_vault", __name__)
        official_bp = Blueprint("web_official_data", __name__)
        reference_bp = Blueprint("web_reference_material", __name__)

        @main_bp.get("/")
        def landing():
            return "landing"

        @overview_bp.get("/overview")
        def overview():
            return "overview"

        @bank_bp.get("/dashboard/bank", endpoint="index")
        def bank_index():
            return "bank"

        @inbox_bp.get("/inbox", endpoint="index")
        def inbox_index():
            return "inbox"

        @auth_bp.get("/login")
        def login():
            return "login"

        @auth_bp.get("/register")
        def register():
            return "register"

        @auth_bp.get("/logout")
        def logout():
            return "logout"

        @calendar_bp.get("/dashboard/calendar")
        def month_calendar():
            return "calendar"

        @vault_bp.get("/dashboard/vault", endpoint="index")
        def vault_index():
            return "vault"

        @official_bp.get("/dashboard/official-data", endpoint="index")
        def official_index():
            return "official"

        @reference_bp.get("/dashboard/reference-materials", endpoint="index")
        def reference_index():
            return "reference"

        app.register_blueprint(main_bp, name="web_main")
        app.register_blueprint(overview_bp, name="web_overview")
        app.register_blueprint(bank_bp, name="web_bank")
        app.register_blueprint(inbox_bp, name="web_inbox")
        app.register_blueprint(auth_bp, name="web_auth")
        app.register_blueprint(calendar_bp, name="web_calendar")
        app.register_blueprint(vault_bp, name="web_vault")
        app.register_blueprint(official_bp, name="web_official_data")
        app.register_blueprint(reference_bp, name="web_reference_material")
        app.register_blueprint(web_billing_bp)

        self.client = app.test_client()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def test_pricing_page_renders_recovered_plan_cards(self) -> None:
        response = self.client.get("/pricing")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("플랜 안내", body)
        self.assertIn("월 6,900원", body)
        self.assertIn("월 12,900원", body)
        self.assertIn("구독 준비 중", body)

    def test_dashboard_billing_requires_login(self) -> None:
        response = self.client.get("/dashboard/billing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/dashboard/billing", response.headers["Location"])

    def test_dashboard_billing_shows_display_only_notice_after_login(self) -> None:
        self._login()

        response = self.client.get("/dashboard/billing")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("구독 안내", body)
        self.assertIn("결제 승인", body)
        self.assertIn("플랜 안내 보기", body)


if __name__ == "__main__":
    unittest.main()
