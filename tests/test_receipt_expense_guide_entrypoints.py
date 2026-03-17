from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReceiptExpenseGuideEntrypointsTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_base_footer_has_global_expense_guide_link(self) -> None:
        body = self._read("templates/base.html")
        self.assertIn('<a href="/guide/expense">비용처리 안내</a>', body)

    def test_review_screen_has_expense_guide_link(self) -> None:
        body = self._read("templates/calendar/review.html")
        self.assertIn("url_for('web_guide.expense_guide')", body)
        self.assertIn("비용처리 안내", body)

    def test_tax_buffer_has_expense_guide_link(self) -> None:
        body = self._read("templates/calendar/tax_buffer.html")
        self.assertIn("url_for('web_guide.expense_guide')", body)
        self.assertIn("어떤 영수증이 비용처리되나요?", body)

    def test_receipt_wizard_retains_contextual_anchor_link(self) -> None:
        body = self._read("templates/calendar/partials/receipt_expense_hint.html")
        self.assertIn("expense_guide_url", body)
        self.assertIn("#{{ expense_guidance.anchor }}", body)
        self.assertIn("왜 이렇게 보나요?", body)


if __name__ == "__main__":
    unittest.main()
