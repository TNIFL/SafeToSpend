from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class TaxSingleStepFlowTest(unittest.TestCase):
    def test_income_classification_is_top_missing_field_before_quick_save(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "annual_gross_income_krw": None,
                "annual_deductible_expense_krw": None,
                "withheld_tax_annual_krw": None,
                "prepaid_tax_annual_krw": None,
                "income_classification": "unknown",
            }
        )
        missing = list(required.get("high_confidence_missing_fields") or [])
        self.assertIn("income_classification", missing)

    def test_after_income_classification_saved_reason_moves_to_next_inputs(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
            }
        )
        missing = list(required.get("high_confidence_missing_fields") or [])
        self.assertNotIn("income_classification", missing)
        self.assertIn("annual_gross_income_krw", missing)
        self.assertIn("annual_deductible_expense_krw", missing)

    def test_quick_single_step_route_and_ui_are_wired(self) -> None:
        profile_route = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        tax_buffer = (ROOT / "templates/calendar/tax_buffer.html").read_text(encoding="utf-8")
        overview = (ROOT / "templates/overview.html").read_text(encoding="utf-8")
        tax_profile = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")

        self.assertIn("tax_income_classification_quick_save", profile_route)
        self.assertIn("소득 유형을 저장했어요", profile_route)
        self.assertIn("missing_income_classification", tax_buffer)
        self.assertIn("소득 유형 먼저 저장", tax_buffer)
        self.assertIn("tax_income_classification_quick_save", overview)
        self.assertIn("소득 유형 1문항", overview)
        self.assertIn("소득 유형 1문항 먼저 저장", tax_profile)


if __name__ == "__main__":
    unittest.main()
