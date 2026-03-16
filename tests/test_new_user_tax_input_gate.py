from __future__ import annotations

import unittest
from pathlib import Path

from services.onboarding import evaluate_tax_required_inputs


ROOT = Path(__file__).resolve().parents[1]


class NewUserTaxInputGateTest(unittest.TestCase):
    def test_profile_route_blocks_when_basic_inputs_missing(self) -> None:
        body = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("기본 입력을 먼저 완료해 주세요", body)
        self.assertIn("세금 기본 입력 단계는 건너뛸 수 없어요", body)

    def test_tax_profile_step2_uses_basic_mode_submit_copy(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertIn("기본 모드(필수)", body)
        self.assertIn("기본 입력 저장 후 다음", body)

    def test_basic_mode_completion_allows_high_confidence(self) -> None:
        out = evaluate_tax_required_inputs(
            {
                "income_classification": "business",
                "annual_gross_income_krw": 66_000_000,
                "annual_deductible_expense_krw": 22_000_000,
                "withheld_tax_annual_krw": 0,
                "prepaid_tax_annual_krw": 0,
                "tax_basic_inputs_confirmed": True,
            }
        )
        self.assertTrue(bool(out.get("high_confidence_inputs_ready")))
        self.assertFalse(bool(out.get("exact_ready_inputs_ready")))


if __name__ == "__main__":
    unittest.main()
