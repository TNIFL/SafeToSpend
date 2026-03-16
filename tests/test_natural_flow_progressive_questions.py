from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NaturalFlowProgressiveQuestionsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_overview_cards_explain_why_extra_input_matters(self) -> None:
        risk_body = self._read("services/risk.py")
        self.assertIn("이 정보 1개만 더 있으면 예상세금이 더 정확해져요", risk_body)
        self.assertIn("이 답변만 끝내면 비용 반영 가능성이 올라가요", risk_body)
        self.assertIn("세무사에게 보낼 자료를 더 분명하게 만들 수 있어요", risk_body)

    def test_review_uses_accuracy_card_not_forceful_prompt(self) -> None:
        body = self._read("templates/calendar/review.html")
        self.assertIn("세금 정확도 올리기", body)
        self.assertIn("돈 받을 때 3.3%가 떼이는지", body)
        self.assertIn("왜 묻나요?", body)
        self.assertIn("기본 정보 이어서 입력", body)
        self.assertIn("지금은 이대로 볼게요", body)

    def test_tax_buffer_uses_result_improvement_copy(self) -> None:
        body = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("세금 정확도 올리기", body)
        self.assertIn("지금 보이는 숫자는 먼저 보여드리고 있고", body)
        self.assertIn("먼저 알려주면 좋은 정보", body)
        self.assertIn("기본 정보 이어서 입력", body)


if __name__ == "__main__":
    unittest.main()
