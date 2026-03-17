from __future__ import annotations

import unittest
from pathlib import Path

from flask import Flask

from routes.web.guide import web_guide_bp


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataGuidePageTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.config["SECRET_KEY"] = "official-data-guide-test"
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

        @app.get("/dashboard/tax-buffer", endpoint="web_calendar.tax_buffer")
        def _tax_buffer():  # pragma: no cover
            return "tax-buffer"

        @app.get("/dashboard/official-data/upload", endpoint="web_official_data.upload_page")
        def _official_data_upload():  # pragma: no cover
            return "official-data-upload"

        self.client = app.test_client()

    def test_official_data_guide_page_renders_hometax_and_nhis_sections(self) -> None:
        resp = self.client.get("/guide/official-data")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("공식 자료 가져오기 안내", body)
        self.assertIn("홈택스 자료", body)
        self.assertIn("NHIS 자료", body)
        self.assertIn("현금영수증 지출증빙 내역", body)
        self.assertIn("보험료 납부확인서", body)
        self.assertIn("실패 시 메뉴 경로", body)
        self.assertIn("기준일이 있는 스냅샷", body)
        self.assertIn("이 자료 바로 올리기", body)
        self.assertIn("law.go.kr", body)


if __name__ == "__main__":
    unittest.main()
