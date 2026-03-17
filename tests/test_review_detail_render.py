from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReviewDetailRenderTest(unittest.TestCase):
    def test_review_template_renders_detail_priority_fields(self) -> None:
        body = (ROOT / "templates/calendar/review.html").read_text(encoding="utf-8")
        self.assertIn("{{ item.display_title }}", body)
        self.assertIn("{{ item.display_amount|krw }}", body)
        self.assertIn("{{ item.display_time }}", body)
        self.assertIn("{{ item.display_account }}", body)
        self.assertIn("{{ item.display_source }}", body)

    def test_review_template_supports_memo_line_and_reason_line(self) -> None:
        body = (ROOT / "templates/calendar/review.html").read_text(encoding="utf-8")
        self.assertIn("{% if item.display_memo %}", body)
        self.assertIn("review-linked-label", body)
        self.assertIn("review-data-sep", body)
        self.assertIn("{{ item.reason }}", body)

    def test_review_template_no_longer_uses_counterparty_or_memo_single_line(self) -> None:
        body = (ROOT / "templates/calendar/review.html").read_text(encoding="utf-8")
        self.assertNotIn("tx.counterparty or tx.memo or", body)


if __name__ == "__main__":
    unittest.main()
