from __future__ import annotations

import unittest
from pathlib import Path

from services.risk import build_tax_result_meta


ROOT = Path(__file__).resolve().parents[1]


class TaxRequiredInputFlowTest(unittest.TestCase):
    def test_tax_meta_requires_basic_user_input_set(self) -> None:
        meta = build_tax_result_meta(None)
        needs = list(meta.get("needs_user_input_fields") or [])
        self.assertIn("income_classification", needs)
        self.assertIn("annual_gross_income_krw", needs)
        self.assertIn("annual_deductible_expense_krw", needs)
        self.assertIn("withheld_tax_annual_krw", needs)
        self.assertIn("prepaid_tax_annual_krw", needs)

    def test_profile_step2_disallows_skip_and_requires_basic_fields(self) -> None:
        body = (ROOT / "routes/web/profile.py").read_text(encoding="utf-8")
        self.assertIn("건너뛸 수 없어요", body)
        self.assertIn("기본 입력", body)

    def test_tax_profile_step2_no_longer_exposes_skip_action(self) -> None:
        body = (ROOT / "templates/tax_profile.html").read_text(encoding="utf-8")
        self.assertNotIn('value="skip"', body)


if __name__ == "__main__":
    unittest.main()
