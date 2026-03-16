from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NaturalFlowCopyTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_copy_guide_keeps_life_language_mapping(self) -> None:
        body = self._read("docs/NATURAL_FLOW_COPY_GUIDE.md")
        self.assertIn("돈 받을 때 미리 빠진 세금", body)
        self.assertIn("일하면서 쓴 비용", body)
        self.assertIn("부가세를 따로 받는 방식", body)
        self.assertIn("아직 검토가 필요해요", body)

    def test_review_and_tax_buffer_replace_front_copy(self) -> None:
        review = self._read("templates/calendar/review.html")
        tax_buffer = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("돈 받을 때 미리 빠진 세금 반영(추정)", review)
        self.assertIn("일하면서 쓴 비용(업무로 확정)", review)
        self.assertIn("내 일 방식/부가세 방식 기준", review)
        self.assertIn("돈 받을 때 미리 빠진 세금 반영(추정)", tax_buffer)
        self.assertIn("일하면서 쓴 비용(업무로 확정)", tax_buffer)
        self.assertIn("내 일 방식/부가세 방식 기준", tax_buffer)

    def test_onboarding_copy_normalizes_not_knowing(self) -> None:
        body = self._read("templates/onboarding.html")
        self.assertIn("대충 골라도 괜찮아요", body)
        self.assertIn("결과부터 보여드릴게요", body)


if __name__ == "__main__":
    unittest.main()
