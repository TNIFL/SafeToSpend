from __future__ import annotations

import unittest
from pathlib import Path

from flask import Flask

from routes.web.legal import web_legal_bp


ROOT = Path(__file__).resolve().parents[1]


class LegalRouteRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.config["SECRET_KEY"] = "legal-routes-test-secret"
        app.register_blueprint(web_legal_bp)

        @app.get("/", endpoint="web_main.landing")
        def _landing():  # pragma: no cover
            return "landing"

        @app.get("/pricing", endpoint="web_main.pricing")
        def _pricing():  # pragma: no cover
            return "pricing"

        @app.get("/preview", endpoint="web_main.preview")
        def _preview():  # pragma: no cover
            return "preview"

        @app.get("/login", endpoint="web_auth.login")
        def _login():  # pragma: no cover
            return "login"

        self.client = app.test_client()

    def test_legal_pages_render_with_footer_links(self) -> None:
        for path, marker in (
            ("/privacy", "개인정보처리방침"),
            ("/terms", "이용약관"),
            ("/disclaimer", "면책고지"),
        ):
            resp = self.client.get(path)
            body = resp.get_data(as_text=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(marker, body)
            self.assertIn("시행일", body)
            self.assertIn("최종 개정일", body)
            self.assertIn('href="/privacy"', body)
            self.assertIn('href="/terms"', body)
            self.assertIn('href="/disclaimer"', body)


class LegalLinkCoverageTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_base_footer_has_legal_links(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn('<a href="/privacy">개인정보처리방침</a>', body)
        self.assertIn('<a href="/terms">이용약관</a>', body)
        self.assertIn('<a href="/disclaimer">면책고지</a>', body)

    def test_required_screens_inherit_base_layout(self) -> None:
        required_templates = (
            "templates/login.html",
            "templates/register.html",
            "templates/pricing.html",
            "templates/mypage.html",
            "templates/billing/register_start.html",
            "templates/billing/register.html",
            "templates/billing/checkout_confirm.html",
            "templates/billing/processing.html",
            "templates/billing/success.html",
            "templates/billing/fail.html",
            "templates/billing/payment_success.html",
            "templates/billing/payment_fail.html",
            "templates/support/form.html",
            "templates/support/my_list.html",
            "templates/support/my_detail.html",
            "templates/support/login_required.html",
        )
        for rel_path in required_templates:
            body = self._read(rel_path)
            self.assertIn('{% extends "base.html" %}', body, msg=rel_path)

    def test_legal_blueprint_registered(self) -> None:
        body = self._read("routes/__init__.py")
        self.assertIn("from routes.web.legal import web_legal_bp", body)
        self.assertIn("app.register_blueprint(web_legal_bp)", body)


if __name__ == "__main__":
    unittest.main()
