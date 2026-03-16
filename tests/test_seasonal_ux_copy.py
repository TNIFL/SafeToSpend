from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SeasonalUxCopyTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_copy_guide_uses_life_language(self) -> None:
        body = self._read("docs/SEASONAL_UX_COPY_GUIDE.md")
        self.assertIn("작년 수입과 비용 정리", body)
        self.assertIn("상반기 기준 미리 점검", body)
        self.assertIn("이미 빠진 세금 확인", body)
        self.assertIn("일하면서 쓴 비용 반영", body)

    def test_service_copy_matches_seasonal_messages(self) -> None:
        body = self._read("services/seasonal_ux.py")
        self.assertIn("이번 시즌은 작년 수입과 비용을 정리하는 달이에요", body)
        self.assertIn("이번 시즌은 상반기 기준으로 미리 점검하는 시기예요", body)
        self.assertIn("이번 달 리듬에 맞춰 필요한 것만 정리해요", body)
        self.assertIn("지금 할 일", self._read("templates/overview.html"))


if __name__ == "__main__":
    unittest.main()
