from __future__ import annotations

import unittest
from datetime import date

from services.seasonal_ux import (
    MAY_FILING_FOCUS,
    NOVEMBER_PREPAYMENT_FOCUS,
    OFF_SEASON,
    build_seasonal_cards,
    build_seasonal_screen_context,
    determine_season_focus,
)


class SeasonalUxStateLogicTest(unittest.TestCase):
    def test_determine_season_focus_by_date(self) -> None:
        self.assertEqual(determine_season_focus(date(2026, 5, 15)), MAY_FILING_FOCUS)
        self.assertEqual(determine_season_focus(date(2026, 11, 10)), NOVEMBER_PREPAYMENT_FOCUS)
        self.assertEqual(determine_season_focus(date(2026, 3, 15)), OFF_SEASON)

    def test_may_cards_reflect_accuracy_and_receipt_state(self) -> None:
        cards = build_seasonal_cards(
            MAY_FILING_FOCUS,
            {
                "has_transactions": True,
                "tax_accuracy_gap": True,
                "profile_completion_percent": 50,
                "receipt_pending_count": 4,
                "reinforcement_pending_count": 1,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 120000,
                "receipt_pending_expense_krw": 88000,
            },
            {
                "review": "/dashboard/review?month=2026-05",
                "tax_buffer": "/dashboard/tax-buffer?month=2026-05",
                "package": "/dashboard/package?month=2026-05",
                "profile": "/dashboard/profile?step=2",
            },
        )
        self.assertEqual(cards[0]["card_type"], "may_accuracy")
        self.assertEqual(cards[0]["completion_state"], "in_progress")
        receipt_card = next(card for card in cards if card["card_type"] == "may_receipt_cleanup")
        self.assertEqual(receipt_card["completion_state"], "in_progress")
        self.assertIn("반영 대기", receipt_card["summary"])

    def test_november_cards_keep_buffer_check_and_review(self) -> None:
        cards = build_seasonal_cards(
            NOVEMBER_PREPAYMENT_FOCUS,
            {
                "has_transactions": True,
                "tax_accuracy_gap": False,
                "profile_completion_percent": 100,
                "receipt_pending_count": 0,
                "reinforcement_pending_count": 0,
                "package_ready": True,
                "package_status": "pass",
                "can_download_package": True,
                "buffer_shortage_krw": 42000,
                "receipt_pending_expense_krw": 0,
            },
            {
                "review": "/dashboard/review?month=2026-11",
                "tax_buffer": "/dashboard/tax-buffer?month=2026-11",
                "package": "/dashboard/package?month=2026-11",
                "profile": "/dashboard/profile?step=2",
            },
        )
        titles = {card["card_type"]: card for card in cards}
        self.assertEqual(titles["november_halfyear_check"]["completion_state"], "done")
        self.assertEqual(titles["november_buffer_check"]["completion_state"], "in_progress")
        self.assertIn("부족", titles["november_buffer_check"]["summary"])

    def test_screen_context_uses_screen_specific_card(self) -> None:
        seasonal_experience = {
            "season_focus": MAY_FILING_FOCUS,
            "season_label": "5월 신고 시즌",
            "strength": "strong",
            "cards": [
                {"card_type": "may_accuracy", "title": "정확도", "summary": "A", "cta_label": "보기", "cta_url": "/a", "completion_state": "todo"},
                {"card_type": "may_receipt_cleanup", "title": "영수증", "summary": "B", "cta_label": "가기", "cta_url": "/b", "completion_state": "in_progress"},
            ],
        }
        review_context = build_seasonal_screen_context(seasonal_experience, "review")
        tax_context = build_seasonal_screen_context(seasonal_experience, "tax_buffer")
        self.assertEqual(review_context["title"], "영수증")
        self.assertEqual(tax_context["title"], "정확도")


if __name__ == "__main__":
    unittest.main()
