from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataEffectsRenderTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_effect_notice_partial_has_reference_date_and_recheck_copy(self) -> None:
        body = self._read("templates/partials/official_data_effect_notice.html")
        self.assertIn("공식 자료 기준으로 보정", body)
        self.assertIn("기준일", body)
        self.assertIn("재확인", body)

    def test_result_overview_and_tax_buffer_include_effect_notice(self) -> None:
        overview = self._read("templates/overview.html")
        tax_buffer = self._read("templates/calendar/tax_buffer.html")
        result = self._read("templates/official_data/result.html")
        self.assertIn('include "partials/official_data_effect_notice.html"', overview)
        self.assertIn('include "partials/official_data_effect_notice.html"', tax_buffer)
        self.assertIn('include "partials/official_data_effect_notice.html"', result)

    def test_user_copy_mentions_before_after_and_snapshot(self) -> None:
        body = self._read("docs/OFFICIAL_DATA_EFFECTS_USER_COPY.md")
        self.assertIn("공식 자료 기준으로 보정", body)
        self.assertIn("기준일", body)
        self.assertIn("새 시즌", body)


if __name__ == "__main__":
    unittest.main()
