from __future__ import annotations

import unittest
from pathlib import Path

from services.seasonal_ux import (
    MAY_FILING_FOCUS,
    NOVEMBER_PREPAYMENT_FOCUS,
    OFF_SEASON,
    build_seasonal_cards,
    seasonal_card_completion_state,
)


ROOT = Path(__file__).resolve().parents[1]


class SeasonalUxMetricsCompletionTest(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8")

    def test_key_cards_define_expected_completion_actions(self) -> None:
        may_cards = {
            card["card_type"]: card
            for card in build_seasonal_cards(
                MAY_FILING_FOCUS,
                {
                    "has_transactions": True,
                    "tax_accuracy_gap": True,
                    "profile_completion_percent": 10,
                    "receipt_pending_count": 4,
                    "reinforcement_pending_count": 1,
                    "package_ready": False,
                    "package_status": "warn",
                    "can_download_package": True,
                    "buffer_shortage_krw": 120000,
                    "receipt_pending_expense_krw": 40000,
                },
                {
                    "review": "/dashboard/review?month=2026-05",
                    "tax_buffer": "/dashboard/tax-buffer?month=2026-05",
                    "package": "/dashboard/package?month=2026-05",
                    "profile": "/dashboard/profile?step=2",
                },
            )
        }
        november_cards = {
            card["card_type"]: card
            for card in build_seasonal_cards(
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
        }
        off_cards = {
            card["card_type"]: card
            for card in build_seasonal_cards(
                OFF_SEASON,
                {
                    "has_transactions": True,
                    "tax_accuracy_gap": True,
                    "profile_completion_percent": 20,
                    "receipt_pending_count": 2,
                    "reinforcement_pending_count": 1,
                    "package_ready": False,
                    "package_status": "warn",
                    "can_download_package": True,
                    "buffer_shortage_krw": 0,
                    "receipt_pending_expense_krw": 0,
                },
                {
                    "review": "/dashboard/review?month=2026-03",
                    "tax_buffer": "/dashboard/tax-buffer?month=2026-03",
                    "package": "/dashboard/package?month=2026-03",
                    "profile": "/dashboard/profile?step=2",
                },
            )
        }

        self.assertEqual(may_cards["may_accuracy"]["completion_action"], "tax_profile_saved")
        self.assertEqual(may_cards["may_receipt_cleanup"]["completion_action"], "review_cleanup_saved")
        self.assertEqual(may_cards["may_package_ready"]["completion_action"], "package_downloaded")
        self.assertEqual(november_cards["november_buffer_check"]["completion_action"], "tax_buffer_adjusted")
        self.assertEqual(november_cards["november_halfyear_check"]["completion_action"], "")
        self.assertEqual(off_cards["offseason_accuracy"]["completion_action"], "tax_profile_saved")

    def test_completion_state_helper_reads_latest_state(self) -> None:
        seasonal_experience = {
            "cards": [
                {"card_type": "may_accuracy", "completion_state": "done"},
                {"card_type": "may_receipt_cleanup", "completion_state": "in_progress"},
            ]
        }
        self.assertEqual(seasonal_card_completion_state(seasonal_experience, "may_accuracy"), "done")
        self.assertEqual(seasonal_card_completion_state(seasonal_experience, "may_receipt_cleanup"), "in_progress")
        self.assertIsNone(seasonal_card_completion_state(seasonal_experience, "missing_card"))

    def test_completed_events_are_recorded_only_on_real_actions(self) -> None:
        overview_route = self._read("routes/web/overview.py")
        profile_route = self._read("routes/web/profile.py")
        review_route = self._read("routes/web/calendar/review.py")
        tax_route = self._read("routes/web/calendar/tax.py")
        package_route = self._read("routes/web/package.py")

        self.assertNotIn("seasonal_card_completed", overview_route)
        self.assertIn("seasonal_card_completed", profile_route)
        self.assertIn("seasonal_card_completed", review_route)
        self.assertIn("seasonal_card_completed", tax_route)
        self.assertIn("seasonal_card_completed", package_route)
        self.assertIn("web_profile.tax_basic_step_save", profile_route)
        self.assertIn("review_expense_followup_save", review_route)
        self.assertIn("tax_buffer_adjust", tax_route)
        self.assertIn("web_package.download", package_route)


if __name__ == "__main__":
    unittest.main()
