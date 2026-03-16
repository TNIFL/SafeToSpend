from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class TaxStepwiseCompletionFlowTest(unittest.TestCase):
    def test_stepwise_route_exists_and_uses_single_field_save(self) -> None:
        body = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("def tax_basic_step_save", body)
        self.assertIn("TAX_BASIC_STEP_ORDER", body)
        self.assertIn("saved_field", body)
        self.assertIn("next_field", body)

    def test_template_exposes_stepwise_single_question_form(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn("기본 입력 단계 저장", body)
        self.assertIn("formaction=\"{{ url_for('web_profile.tax_basic_step_save') }}\"", body)
        self.assertIn("tax_stepwise.next_field", body)

    def test_all_basic_fields_without_confirmation_do_not_promote(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 12_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": False,
            }
        )
        self.assertFalse(bool(required.get("high_confidence_inputs_ready")))
        self.assertIn("tax_basic_inputs_confirmed", list(required.get("high_confidence_missing_fields") or []))

    def test_all_basic_fields_with_confirmation_promote_high_confidence(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 50_000_000,
                "annual_deductible_expense_krw": 12_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
            }
        )
        self.assertTrue(bool(required.get("high_confidence_inputs_ready")))


if __name__ == "__main__":
    unittest.main()
