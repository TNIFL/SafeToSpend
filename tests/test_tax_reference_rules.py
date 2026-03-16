from __future__ import annotations

import unittest

from services.reference.tax_reference import (
    calculate_local_income_tax,
    calculate_national_income_tax,
    get_tax_reference_snapshot,
)


class TaxReferenceRulesTest(unittest.TestCase):
    def test_reference_constants_exact(self) -> None:
        ref = get_tax_reference_snapshot(2026)
        self.assertEqual(float(ref.local_income_tax_ratio), 0.10)
        self.assertGreaterEqual(len(ref.income_tax_brackets), 8)
        first = ref.income_tax_brackets[0]
        self.assertEqual(int(first.upper_limit_krw), 14_000_000)
        self.assertEqual(float(first.rate), 0.06)
        self.assertEqual(int(first.progressive_deduction_krw), 0)

    def test_national_tax_vectors_exact(self) -> None:
        self.assertEqual(
            calculate_national_income_tax(taxable_income_krw=20_000_000, target_year=2026),
            1_740_000,
        )
        self.assertEqual(
            calculate_national_income_tax(taxable_income_krw=13_000_000, target_year=2026),
            780_000,
        )
        self.assertEqual(
            calculate_national_income_tax(taxable_income_krw=100_000_000, target_year=2026),
            19_560_000,
        )

    def test_local_tax_ratio_exact(self) -> None:
        self.assertEqual(calculate_local_income_tax(national_income_tax_krw=1_740_000, target_year=2026), 174_000)
        self.assertEqual(calculate_local_income_tax(national_income_tax_krw=780_000, target_year=2026), 78_000)
        self.assertEqual(calculate_local_income_tax(national_income_tax_krw=19_560_000, target_year=2026), 1_956_000)


if __name__ == "__main__":
    unittest.main()
