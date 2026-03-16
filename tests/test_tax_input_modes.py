from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class TaxInputModesTest(unittest.TestCase):
    def test_template_uses_user_language_for_basic_mode(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn("기본 모드(필수)", body)
        self.assertIn("총수입(필수)", body)
        self.assertIn("업무 관련 지출(필수)", body)
        self.assertIn("이미 떼인 세금(원천징수, 필수)", body)
        self.assertIn("이미 낸 세금(기납부, 필수)", body)
        self.assertIn("소득 유형(필수)", body)

    def test_template_separates_advanced_taxable_input(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn("고급 입력(선택): 연 과세표준", body)
        self.assertIn("confirm_advanced_taxable_input", body)
        self.assertIn("exact_ready", body)

    def test_route_requires_advanced_confirmation_when_taxable_is_used(self) -> None:
        body = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("confirm_advanced_taxable_input", body)
        self.assertIn("고급 입력을 사용할 때는 연 과세표준을 0보다 큰 값으로 입력", body)

    def test_basic_inputs_only_can_reach_high_confidence(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 58_000_000,
                "annual_deductible_expense_krw": 20_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
            }
        )
        self.assertTrue(bool(required.get("high_confidence_inputs_ready")))
        self.assertFalse(bool(required.get("exact_ready_inputs_ready")))

    def test_advanced_input_confirmation_unlocks_exact_ready(self) -> None:
        required = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 58_000_000,
                "annual_deductible_expense_krw": 20_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
                "official_taxable_income_annual_krw": 19_000_000,
                "tax_advanced_input_confirmed": True,
            }
        )
        self.assertTrue(bool(required.get("exact_ready_inputs_ready")))


if __name__ == "__main__":
    unittest.main()
