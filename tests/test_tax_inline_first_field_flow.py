from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class TaxInlineFirstFieldFlowTest(unittest.TestCase):
    def test_missing_income_classification_is_blocking_reason_candidate(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "unknown",
                "annual_gross_income_krw": None,
                "annual_deductible_expense_krw": None,
                "withheld_tax_annual_krw": None,
                "prepaid_tax_annual_krw": None,
            }
        )
        self.assertIn("income_classification", list(required.get("high_confidence_missing_fields") or []))

    def test_inline_income_classification_card_is_rendered_on_key_surfaces(self) -> None:
        overview = (ROOT / "templates/overview.html").read_text(encoding="utf-8")
        tax_buffer = (ROOT / "templates/calendar/tax_buffer.html").read_text(encoding="utf-8")
        tax_profile = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")

        self.assertIn("tax_result_meta and tax_result_meta.reason == 'missing_income_classification'", overview)
        self.assertIn("tax_income_classification_quick_save", overview)
        self.assertIn("소득 유형 1문항", overview)
        self.assertIn("바로 저장", overview)

        self.assertIn("tax_calc_meta and tax_calc_meta.reason == 'missing_income_classification'", tax_buffer)
        self.assertIn("tax_income_classification_quick_save", tax_buffer)
        self.assertIn("소득 유형 1문항만 먼저 저장", tax_buffer)

        self.assertIn("tax_stepwise.next_field == 'income_classification'", tax_profile)
        self.assertIn("formaction=\"{{ url_for('web_profile.tax_basic_step_save') }}\"", tax_profile)

    def test_quick_save_route_recalculates_and_routes_to_next_step(self) -> None:
        profile_route = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("event=\"tax_inline_income_classification_saved\"", profile_route)
        self.assertIn("event=\"tax_basic_next_step_viewed\"", profile_route)
        self.assertIn("inline_saved=\"income_classification\"", profile_route)
        self.assertIn("focus=(next_field or \"\")", profile_route)


if __name__ == "__main__":
    unittest.main()
