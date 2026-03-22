from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from flask import Blueprint, Flask

from routes.web.package import web_package_bp
from routes.web.web_calendar import web_calendar_bp


class PackageRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        app.secret_key = "test-secret"

        auth_bp = Blueprint("web_auth", __name__)

        @auth_bp.get("/login")
        def login():
            return "login"

        app.register_blueprint(auth_bp, url_prefix="/auth")
        app.register_blueprint(web_package_bp)
        app.register_blueprint(web_calendar_bp)
        self.client = app.test_client()

    def _login(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = 7

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
