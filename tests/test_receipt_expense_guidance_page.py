from __future__ import annotations

import unittest
from pathlib import Path

from flask import Flask

from routes.web.guide import web_guide_bp


ROOT = Path(__file__).resolve().parents[1]


class ReceiptExpenseGuidancePageTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.config["SECRET_KEY"] = "receipt-guidance-test"
        app.register_blueprint(web_guide_bp)

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

    def test_expense_guide_page_renders_core_sections(self) -> None:
        resp = self.client.get("/guide/expense")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("비용처리 안내", body)
        self.assertIn("비용처리 가능성이 높은 항목", body)
        self.assertIn("추가 확인이 필요한 항목", body)
        self.assertIn("자동 인정하지 않는 항목", body)
        self.assertIn("세무 검토 권장 항목", body)
        self.assertIn("서비스의 분류 결과는 보조 판단입니다.", body)
        self.assertIn("자주 헷갈리는 사례", body)
        self.assertIn("law.go.kr", body)


if __name__ == "__main__":
    unittest.main()
