from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask

from routes.web.package import web_package_bp
from routes.web.web_calendar import web_calendar_bp


class _DummyQuery:
    def __init__(self, *, rows=None, scalar_value=None) -> None:
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value


class PackageRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        template_folder = str(Path(__file__).resolve().parents[1] / "templates")
        app = Flask(__name__, template_folder=template_folder)
        app.secret_key = "test-secret"

        main_bp = Blueprint("web_main", __name__)
        overview_bp = Blueprint("web_overview", __name__)
        dashboard_bp = Blueprint("web_dashboard", __name__)
        bank_bp = Blueprint("web_bank", __name__)
        inbox_bp = Blueprint("web_inbox", __name__)
        auth_bp = Blueprint("web_auth", __name__)
        vault_bp = Blueprint("web_vault", __name__)
        billing_bp = Blueprint("web_billing", __name__)
        profile_bp = Blueprint("web_profile", __name__)
        support_bp = Blueprint("web_support", __name__)
        official_bp = Blueprint("web_official_data", __name__)
        reference_bp = Blueprint("web_reference_material", __name__)
        receipt_modal_bp = Blueprint("web_receipt_modal", __name__)

        @main_bp.get("/")
        def landing():
            return "landing"

        @overview_bp.get("/overview", endpoint="overview")
        def overview():
            return "overview"

        @dashboard_bp.get("/dashboard/", endpoint="index")
        def dashboard_index():
            return "dashboard"

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

        @vault_bp.get("/dashboard/vault", endpoint="index")
        def vault_index():
            return "vault"

        @billing_bp.get("/pricing", endpoint="pricing_page")
        def pricing_page():
            return "pricing"

        @billing_bp.get("/dashboard/billing", endpoint="index")
        def billing_index():
            return "billing"

        @profile_bp.get("/mypage", endpoint="mypage")
        def profile_mypage():
            return "mypage"

        @support_bp.get("/support", endpoint="support_home")
        def support_home():
            return "support"

        @official_bp.get("/dashboard/official-data", endpoint="index")
        def official_index():
            return "official"

        @reference_bp.get("/dashboard/reference-materials", endpoint="index")
        def reference_index():
            return "reference"

        @receipt_modal_bp.post("/dashboard/receipt-modal/start", endpoint="start")
        def receipt_start():
            return "start"

        @receipt_modal_bp.get("/dashboard/receipt-modal/jobs/<job_id>", endpoint="job_status")
        def receipt_status(job_id: str):
            return job_id

        @receipt_modal_bp.post("/dashboard/receipt-modal/jobs/<job_id>/create", endpoint="create")
        def receipt_create(job_id: str):
            return job_id

        @receipt_modal_bp.get("/dashboard/receipt-modal/history", endpoint="history")
        def receipt_history():
            return "history"

        @receipt_modal_bp.post("/dashboard/receipt-modal/jobs/<job_id>/items/<item_id>/save", endpoint="save_item")
        def receipt_save(job_id: str, item_id: str):
            return f"{job_id}:{item_id}"

        app.register_blueprint(main_bp, name="web_main")
        app.register_blueprint(overview_bp, name="web_overview")
        app.register_blueprint(dashboard_bp, name="web_dashboard")
        app.register_blueprint(bank_bp, name="web_bank")
        app.register_blueprint(inbox_bp, name="web_inbox")
        app.register_blueprint(auth_bp, url_prefix="/auth")
        app.register_blueprint(vault_bp, name="web_vault")
        app.register_blueprint(billing_bp, name="web_billing")
        app.register_blueprint(profile_bp, name="web_profile")
        app.register_blueprint(support_bp, name="web_support")
        app.register_blueprint(official_bp, name="web_official_data")
        app.register_blueprint(reference_bp, name="web_reference_material")
        app.register_blueprint(receipt_modal_bp, name="web_receipt_modal")
        app.register_blueprint(web_package_bp)
        app.register_blueprint(web_calendar_bp)
        self.client = app.test_client()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def _page_queries(self) -> list[_DummyQuery]:
        return [
            _DummyQuery(rows=[]),
            _DummyQuery(scalar_value=1200000),
            _DummyQuery(scalar_value=450000),
            _DummyQuery(scalar_value=3),
        ]

    @patch("routes.web.package.build_tax_package_zip")
    def test_package_download_route_uses_single_package_builder(self, build_zip) -> None:
        build_zip.return_value = (io.BytesIO(b"zip-bytes"), "세무사전달패키지_2026-03_테스터.zip")
        self._login()

        response = self.client.get("/dashboard/package/download?month=2026-03")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("2026-03", response.headers["Content-Disposition"])
        build_zip.assert_called_once_with(user_pk=7, month_key="2026-03", profile_code=None)

    @patch("routes.web.package.build_tax_package_zip")
    def test_package_download_route_passes_profile_code(self, build_zip) -> None:
        build_zip.return_value = (io.BytesIO(b"zip-bytes"), "세무사전달패키지_부가세용_2026-03_테스터.zip")
        self._login()

        response = self.client.get("/dashboard/package/download?month=2026-03&profile=vat_review")

        self.assertEqual(response.status_code, 200)
        build_zip.assert_called_once_with(user_pk=7, month_key="2026-03", profile_code="vat_review")

    @patch("routes.web.package._ensure_month_evidence_rows")
    @patch("routes.web.package.db.session.query")
    def test_package_page_defaults_to_common_profile(self, query_mock, ensure_rows) -> None:
        query_mock.side_effect = self._page_queries()
        self._login()

        response = self.client.get("/dashboard/package?month=2026-03")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-selected-profile="common"', body)
        self.assertIn('data-profile-option="common"', body)
        self.assertIn("공통형 다운로드", body)
        self.assertIn("전체 자료를 종합적으로 점검하는 기본 패키지", body)
        self.assertIn("/dashboard/package?month=2026-02&amp;profile=common", body)
        self.assertIn("/dashboard/package/download?month=2026-03&amp;profile=common", body)
        ensure_rows.assert_called_once()

    @patch("routes.web.package._ensure_month_evidence_rows")
    @patch("routes.web.package.db.session.query")
    def test_package_page_renders_profile_cards_and_keeps_selected_profile_links(self, query_mock, ensure_rows) -> None:
        query_mock.side_effect = self._page_queries()
        self._login()

        response = self.client.get("/dashboard/package?month=2026-03&profile=vat_review")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-selected-profile="vat_review"', body)
        self.assertIn('data-profile-option="common"', body)
        self.assertIn('data-profile-option="comprehensive_income"', body)
        self.assertIn('data-profile-option="vat_review"', body)
        self.assertIn('data-profile-option="nhis_pension_check"', body)
        self.assertIn("전체 자료를 종합적으로 점검하는 기본 패키지", body)
        self.assertIn("원천징수·기납부세액·거래/증빙 검토를 우선 보는 패키지", body)
        self.assertIn("부가세 관련 자료와 재확인 항목을 우선 보는 패키지", body)
        self.assertIn("건강보험·국민연금 관련 자료와 재확인 포인트를 먼저 보는 패키지", body)
        self.assertIn("부가세용 다운로드", body)
        self.assertIn("/dashboard/package?month=2026-02&amp;profile=vat_review", body)
        self.assertIn("/dashboard/package/download?month=2026-03&amp;profile=vat_review", body)
        ensure_rows.assert_called_once()

    def test_tax_package_route_redirects_to_package_download(self) -> None:
        self._login()

        response = self.client.get("/dashboard/tax-package?month=2026-03", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard/package/download?month=2026-03"))

    def test_tax_package_route_keeps_profile_query_when_present(self) -> None:
        self._login()

        response = self.client.get("/dashboard/tax-package?month=2026-03&profile=comprehensive_income", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/dashboard/package/download?month=2026-03&profile=comprehensive_income")
        )
