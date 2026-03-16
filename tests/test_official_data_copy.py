from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialDataCopyTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_upload_notice_has_why_benefit_and_storage_summary(self) -> None:
        body = self._read("templates/partials/official_data_upload_notice.html")
        self.assertIn("왜 필요한가", body)
        self.assertIn("무엇이 좋아지나", body)
        self.assertIn("저장 방식 요약", body)
        self.assertNotIn("영구 자동화", body)

    def test_storage_notice_has_basis_date_recheck_and_storage_mode(self) -> None:
        body = self._read("templates/partials/official_data_storage_notice.html")
        self.assertIn("기준일", body)
        self.assertIn("다시 확인", body)
        self.assertIn("핵심 추출값", body)
        self.assertIn("원본 파일", body)

    def test_guide_page_uses_life_language_copy(self) -> None:
        body = self._read("templates/guide/official-data-guide.html")
        self.assertIn("공식 자료 가져오기 안내", body)
        self.assertIn("어떤 숫자가 좋아지는지", body)
        self.assertIn("실패 시 메뉴 경로", body)


if __name__ == "__main__":
    unittest.main()
