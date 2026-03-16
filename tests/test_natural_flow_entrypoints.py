from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NaturalFlowEntrypointsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_onboarding_redirects_to_overview_result_first(self) -> None:
        body = self._read("routes/web/auth.py")
        self.assertIn('flash("설정이 저장되었습니다. 지금 상태 기준 결과를 먼저 보여드릴게요."', body)
        self.assertIn('return redirect(url_for("web_overview.overview"))', body)

    def test_onboarding_template_uses_result_first_copy(self) -> None:
        body = self._read("templates/onboarding.html")
        self.assertIn("결과부터 보여드릴게요", body)
        self.assertIn("더 정확하게 만드는 질문은 결과를 본 뒤", body)
        self.assertIn("저장하고 결과 보기", body)

    def test_review_and_tax_buffer_accuracy_cta_go_to_basic_step(self) -> None:
        review_route = self._read("routes/web/calendar/review.py")
        tax_route = self._read("routes/web/calendar/tax.py")
        self.assertIn('step=2', review_route)
        self.assertIn('recovery_source="review_accuracy_card"', review_route)
        self.assertIn('"step": 2', tax_route)

    def test_overview_has_result_improvement_cards(self) -> None:
        body = self._read("templates/overview.html")
        self.assertIn("결과 더 좋아지게 만들기", body)
        self.assertIn("improvement_cards", body)
        self.assertIn("세금 정확도", body)
        self.assertIn("반영 가능성", body)
        self.assertIn("전달 품질", body)


if __name__ == "__main__":
    unittest.main()
