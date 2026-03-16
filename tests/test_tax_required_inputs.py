from __future__ import annotations

import unittest

from services.onboarding import evaluate_tax_required_inputs


class TaxRequiredInputsTest(unittest.TestCase):
    def test_exact_ready_inputs_when_all_required_fields_present(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 60_000_000,
                "annual_deductible_expense_krw": 18_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
                "official_taxable_income_annual_krw": 20_000_000,
                "tax_advanced_input_confirmed": True,
            }
        )
        self.assertTrue(bool(out.get("high_confidence_inputs_ready")))
        self.assertTrue(bool(out.get("exact_ready_inputs_ready")))
        self.assertEqual(list(out.get("exact_ready_missing_fields") or []), [])

    def test_missing_income_classification_downgrades_to_not_high_confidence(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "annual_gross_income_krw": 48_000_000,
                "annual_deductible_expense_krw": 8_000_000,
                "withheld_tax_annual_krw": 100_000,
                "prepaid_tax_annual_krw": 10_000,
                "tax_basic_inputs_confirmed": True,
                "income_classification": "unknown",
            }
        )
        self.assertFalse(bool(out.get("high_confidence_inputs_ready")))
        self.assertIn("income_classification", list(out.get("high_confidence_missing_fields") or []))

    def test_missing_advanced_input_blocks_exact_only(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 52_000_000,
                "annual_deductible_expense_krw": 12_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
            }
        )
        self.assertTrue(bool(out.get("high_confidence_inputs_ready")))
        self.assertFalse(bool(out.get("exact_ready_inputs_ready")))
        self.assertIn("official_taxable_income_annual_krw", list(out.get("exact_ready_missing_fields") or []))


if __name__ == "__main__":
    unittest.main()
