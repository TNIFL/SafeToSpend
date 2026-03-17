from __future__ import annotations

import unittest
from pathlib import Path

from services.seasonal_ux import OFF_SEASON, build_seasonal_cards, build_seasonal_screen_context


ROOT = Path(__file__).resolve().parents[1]


class SeasonalUxPriorityAdjustmentsTest(unittest.TestCase):
    def _facts(self) -> dict[str, object]:
        return {
            "has_transactions": True,
            "tax_accuracy_gap": True,
            "profile_completion_percent": 20,
            "receipt_pending_count": 3,
            "reinforcement_pending_count": 1,
            "package_ready": False,
            "package_status": "warn",
            "can_download_package": True,
            "buffer_shortage_krw": 80000,
            "receipt_pending_expense_krw": 42000,
        }

    def _urls(self) -> dict[str, str]:
        return {
            "review": "/dashboard/review?month=2026-03",
            "tax_buffer": "/dashboard/tax-buffer?month=2026-03",
            "package": "/dashboard/package?month=2026-03",
            "profile": "/dashboard/profile?step=2",
        }

    def test_offseason_priority_order_is_unchanged(self) -> None:
        cards = build_seasonal_cards(OFF_SEASON, self._facts(), self._urls())
        self.assertEqual(
            [card["card_type"] for card in cards],
            ["offseason_monthly_review", "offseason_accuracy", "offseason_package_ready"],
        )
        review_card = next(card for card in cards if card["card_type"] == "offseason_monthly_review")
        self.assertEqual(review_card["priority"], 0)

    def test_offseason_accuracy_cta_is_more_concrete(self) -> None:
        cards = build_seasonal_cards(OFF_SEASON, self._facts(), self._urls())
        accuracy_card = next(card for card in cards if card["card_type"] == "offseason_accuracy")
        self.assertEqual(accuracy_card["cta_label"], "3.3%·빠진 세금 확인하기")
        self.assertEqual(accuracy_card["cta_target"], "profile")
        self.assertIn("3.3%", accuracy_card["summary"])

    def test_same_screen_context_cta_uses_specific_anchor_labels(self) -> None:
        cards = build_seasonal_cards(OFF_SEASON, self._facts(), self._urls())
        tax_buffer_ready_facts = dict(self._facts())
        tax_buffer_ready_facts["tax_accuracy_gap"] = False
        tax_buffer_ready_facts["profile_completion_percent"] = 100
        tax_cards = build_seasonal_cards(OFF_SEASON, tax_buffer_ready_facts, self._urls())
        experience = {
            "season_focus": OFF_SEASON,
            "season_label": "비시즌",
            "strength": "soft",
            "cards": cards,
            "facts": self._facts(),
        }
        tax_experience = {
            "season_focus": OFF_SEASON,
            "season_label": "비시즌",
            "strength": "soft",
            "cards": tax_cards,
            "facts": tax_buffer_ready_facts,
        }

        review_context = build_seasonal_screen_context(experience, "review")
        tax_context = build_seasonal_screen_context(tax_experience, "tax_buffer")
        package_context = build_seasonal_screen_context(experience, "package")

        self.assertEqual(review_context["cta_label"], "반영 대기 항목부터 정리하기")
        self.assertTrue(str(review_context["cta_url"]).endswith("#review-worklist"))
        self.assertIn("follow-up", review_context["summary"])

        self.assertEqual(tax_context["cta_label"], "예상세금·보관액 바로 보기")
        self.assertTrue(str(tax_context["cta_url"]).endswith("#tax-buffer-kpis"))
        self.assertIn("예상세금", tax_context["summary"])

        self.assertEqual(package_context["cta_label"], "세무사 보내기 전 마지막 점검 보기")
        self.assertTrue(str(package_context["cta_url"]).endswith("#package-readiness"))
        self.assertIn("전달 준비 상태", package_context["summary"])

    def test_templates_expose_matching_anchor_targets(self) -> None:
        review_template = (ROOT / "templates/calendar/review.html").read_text(encoding="utf-8")
        tax_template = (ROOT / "templates/calendar/tax_buffer.html").read_text(encoding="utf-8")
        package_template = (ROOT / "templates/package/index.html").read_text(encoding="utf-8")

        self.assertIn('id="review-worklist"', review_template)
        self.assertIn('id="review-summary-progress"', review_template)
        self.assertIn('id="tax-buffer-kpis"', tax_template)
        self.assertIn('id="package-readiness"', package_template)


if __name__ == "__main__":
    unittest.main()
