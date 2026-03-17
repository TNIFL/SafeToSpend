from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class TaxInputRecoveryFlowTest(unittest.TestCase):
    def test_blocked_profile_missing_required_inputs(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "unknown",
            }
        )
        self.assertFalse(bool(out.get("high_confidence_inputs_ready")))
        missing = list(out.get("high_confidence_missing_fields") or [])
        self.assertIn("income_classification", missing)
        self.assertIn("annual_gross_income_krw", missing)
        self.assertIn("annual_deductible_expense_krw", missing)
        self.assertIn("withheld_tax_annual_krw", missing)
        self.assertIn("prepaid_tax_annual_krw", missing)

    def test_basic_required_inputs_complete_promotes_to_high_confidence(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 64_000_000,
                "annual_deductible_expense_krw": 16_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
            }
        )
        self.assertTrue(bool(out.get("high_confidence_inputs_ready")))
        self.assertFalse(bool(out.get("exact_ready_inputs_ready")))

    def test_advanced_input_save_promotes_to_exact_ready(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 64_000_000,
                "annual_deductible_expense_krw": 16_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
                "official_taxable_income_annual_krw": 22_000_000,
                "tax_advanced_input_confirmed": True,
            }
        )
        self.assertTrue(bool(out.get("exact_ready_inputs_ready")))

    def test_proxy_autofill_only_does_not_promote(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 64_000_000,
                "annual_deductible_expense_krw": 16_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
            }
        )
        self.assertFalse(bool(out.get("high_confidence_inputs_ready")))
        self.assertIn("tax_basic_inputs_confirmed", list(out.get("high_confidence_missing_fields") or []))

    def test_tax_profile_template_has_recovery_prefill_copy(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn("초안", body)
        self.assertIn("저장해야", body)

    def test_tax_profile_route_uses_refined_zero_input_guidance(self) -> None:
        body = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("기본 입력", body)
        self.assertIn("build_tax_input_draft", body)


if __name__ == "__main__":
    unittest.main()
