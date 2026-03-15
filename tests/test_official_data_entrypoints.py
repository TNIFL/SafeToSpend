from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataEntrypointsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_entrypoint_partial_links_to_official_data_guide(self) -> None:
        body = self._read("templates/partials/official_data_entrypoints.html")
        self.assertIn("url_for('web_guide.official_data_guide')", body)
        self.assertIn("official_data_upload_href", body)
        self.assertIn("공식 자료 안내 보기", body)

    def test_overview_tax_buffer_and_nhis_include_entrypoint(self) -> None:
        overview = self._read("templates/overview.html")
        tax_buffer = self._read("templates/calendar/tax_buffer.html")
        nhis = self._read("templates/nhis.html")
        self.assertIn('include "partials/official_data_entrypoints.html"', overview)
        self.assertIn('include "partials/official_data_entrypoints.html"', tax_buffer)
        self.assertIn('include "partials/official_data_entrypoints.html"', nhis)
        self.assertIn("공식 자료로 숫자 보정하기", overview)
        self.assertIn("홈택스 자료 가져오기 안내", tax_buffer)
        self.assertIn("NHIS 자료 가져오기 안내", nhis)
        self.assertIn("web_official_data.upload_page", overview)
        self.assertIn("web_official_data.upload_page", tax_buffer)
        self.assertIn("web_official_data.upload_page", nhis)


if __name__ == "__main__":
    unittest.main()
