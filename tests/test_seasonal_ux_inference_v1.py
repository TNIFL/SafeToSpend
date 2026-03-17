from __future__ import annotations

import unittest

from services.seasonal_ux import (
    MAY_FILING_FOCUS,
    NOVEMBER_PREPAYMENT_FOCUS,
    OFF_SEASON,
    SEASONAL_UX_ALLOWED_INFERENCE_SIGNALS,
    SEASONAL_UX_FORBIDDEN_INFERENCE_SIGNALS,
    build_seasonal_cards,
    build_seasonal_screen_context,
)


class SeasonalUxInferenceV1Test(unittest.TestCase):
    def _urls(self) -> dict[str, str]:
        return {
            "review": "/dashboard/review?month=2026-03",
            "tax_buffer": "/dashboard/tax-buffer?month=2026-03",
            "package": "/dashboard/package?month=2026-03",
            "profile": "/dashboard/profile?step=2",
        }

    def test_allowed_and_forbidden_signal_sets_are_declared(self) -> None:
        self.assertIn("receipt_pending_count", SEASONAL_UX_ALLOWED_INFERENCE_SIGNALS)
        self.assertIn("tax_accuracy_gap", SEASONAL_UX_ALLOWED_INFERENCE_SIGNALS)
        self.assertIn("package_ready", SEASONAL_UX_ALLOWED_INFERENCE_SIGNALS)
        self.assertIn("guessed_withholding_from_patterns", SEASONAL_UX_FORBIDDEN_INFERENCE_SIGNALS)
        self.assertIn("guessed_vat_type", SEASONAL_UX_FORBIDDEN_INFERENCE_SIGNALS)

    def test_review_card_gets_priority_boost_when_pending_signals_exist(self) -> None:
        cards = build_seasonal_cards(
            MAY_FILING_FOCUS,
            {
                "has_transactions": True,
                "tax_accuracy_gap": False,
                "profile_completion_percent": 100,
                "receipt_pending_count": 4,
                "reinforcement_pending_count": 1,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 0,
                "receipt_pending_expense_krw": 120000,
            },
            self._urls(),
        )
        review_card = next(card for card in cards if card["card_type"] == "may_receipt_cleanup")
        accuracy_card = next(card for card in cards if card["card_type"] == "may_accuracy")

        self.assertEqual(review_card["priority_base"], 2)
        self.assertEqual(review_card["priority_effective"], 1)
        self.assertGreater(review_card["priority_adjustment_score"], 0)
        self.assertIn("pending_backlog_present", review_card["priority_adjustment_reasons"])
        self.assertEqual(cards[0]["card_type"], "may_receipt_cleanup")
        self.assertEqual(accuracy_card["priority_effective"], 1)

    def test_accuracy_card_gets_boost_when_tax_gap_exists(self) -> None:
        cards = build_seasonal_cards(
            NOVEMBER_PREPAYMENT_FOCUS,
            {
                "has_transactions": True,
                "tax_accuracy_gap": True,
                "profile_completion_percent": 20,
                "receipt_pending_count": 0,
                "reinforcement_pending_count": 0,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 50000,
                "receipt_pending_expense_krw": 0,
            },
            self._urls(),
        )
        accuracy_card = next(card for card in cards if card["card_type"] == "november_halfyear_check")
        self.assertEqual(accuracy_card["priority_base"], 1)
        self.assertEqual(accuracy_card["priority_effective"], 0)
        self.assertEqual(accuracy_card["priority_adjustment_reason"], "tax_accuracy_gap")
        self.assertEqual(cards[0]["card_type"], "november_halfyear_check")

    def test_package_card_gets_low_risk_boost_only_when_ready_and_backlog_is_low(self) -> None:
        cards = build_seasonal_cards(
            OFF_SEASON,
            {
                "has_transactions": True,
                "tax_accuracy_gap": False,
                "profile_completion_percent": 100,
                "receipt_pending_count": 0,
                "reinforcement_pending_count": 0,
                "package_ready": True,
                "package_status": "pass",
                "can_download_package": True,
                "buffer_shortage_krw": 0,
                "receipt_pending_expense_krw": 0,
            },
            self._urls(),
        )
        package_card = next(card for card in cards if card["card_type"] == "offseason_package_ready")
        self.assertEqual(package_card["priority_base"], 3)
        self.assertEqual(package_card["priority_effective"], 2)
        self.assertIn("package_ready_with_low_pending", package_card["priority_adjustment_reasons"])

    def test_no_signal_keeps_base_priority(self) -> None:
        cards = build_seasonal_cards(
            OFF_SEASON,
            {
                "has_transactions": True,
                "tax_accuracy_gap": False,
                "profile_completion_percent": 100,
                "receipt_pending_count": 0,
                "reinforcement_pending_count": 0,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 0,
                "receipt_pending_expense_krw": 0,
            },
            self._urls(),
        )
        accuracy_card = next(card for card in cards if card["card_type"] == "offseason_accuracy")
        self.assertEqual(accuracy_card["priority_base"], 2)
        self.assertEqual(accuracy_card["priority_effective"], 2)
        self.assertEqual(accuracy_card["priority_adjustment_score"], 0)

    def test_forbidden_signal_input_does_not_change_result(self) -> None:
        base_facts = {
            "has_transactions": True,
            "tax_accuracy_gap": False,
            "profile_completion_percent": 100,
            "receipt_pending_count": 1,
            "reinforcement_pending_count": 0,
            "package_ready": False,
            "package_status": "warn",
            "can_download_package": True,
            "buffer_shortage_krw": 0,
            "receipt_pending_expense_krw": 0,
        }
        with_forbidden = dict(base_facts)
        with_forbidden["guessed_withholding_from_patterns"] = True
        with_forbidden["guessed_vat_type"] = "general"
        with_forbidden["guessed_prepaid_tax_level"] = "high"

        base_cards = build_seasonal_cards(OFF_SEASON, base_facts, self._urls())
        forbidden_cards = build_seasonal_cards(OFF_SEASON, with_forbidden, self._urls())

        self.assertEqual(
            [(card["card_type"], card["priority_effective"], card["priority_adjustment_reason"]) for card in base_cards],
            [(card["card_type"], card["priority_effective"], card["priority_adjustment_reason"]) for card in forbidden_cards],
        )

    def test_screen_context_keeps_explainability_metadata(self) -> None:
        cards = build_seasonal_cards(
            OFF_SEASON,
            {
                "has_transactions": True,
                "tax_accuracy_gap": True,
                "profile_completion_percent": 30,
                "receipt_pending_count": 2,
                "reinforcement_pending_count": 0,
                "package_ready": False,
                "package_status": "warn",
                "can_download_package": True,
                "buffer_shortage_krw": 20000,
                "receipt_pending_expense_krw": 0,
            },
            self._urls(),
        )
        experience = {
            "season_focus": OFF_SEASON,
            "season_label": "비시즌",
            "strength": "soft",
            "cards": cards,
            "facts": {
                "receipt_pending_count": 2,
                "reinforcement_pending_count": 0,
                "buffer_shortage_krw": 20000,
            },
        }
        context = build_seasonal_screen_context(experience, "tax_buffer")
        self.assertIn("priority_adjustment_score", context)
        self.assertIn("priority_adjustment_reason", context)


if __name__ == "__main__":
    unittest.main()
