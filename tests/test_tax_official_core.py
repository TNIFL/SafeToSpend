from __future__ import annotations

import unittest

from services.tax_official_core import compute_tax_official_core


class TaxOfficialCoreTest(unittest.TestCase):
    def test_missing_taxable_income_blocks(self) -> None:
        out = compute_tax_official_core(taxable_income_annual_krw=0, target_year=2026)
        self.assertFalse(bool(out.calculable))
        self.assertEqual(str(out.reason), "missing_taxable_income")

    def test_official_vectors_exact(self) -> None:
        out_20m = compute_tax_official_core(taxable_income_annual_krw=20_000_000, target_year=2026)
        self.assertTrue(bool(out_20m.calculable))
        self.assertEqual(int(out_20m.national_tax_annual_krw), 1_740_000)
        self.assertEqual(int(out_20m.local_tax_annual_krw), 174_000)

        out_13m = compute_tax_official_core(taxable_income_annual_krw=13_000_000, target_year=2026)
        self.assertEqual(int(out_13m.national_tax_annual_krw), 780_000)
        self.assertEqual(int(out_13m.local_tax_annual_krw), 78_000)

        out_100m = compute_tax_official_core(taxable_income_annual_krw=100_000_000, target_year=2026)
        self.assertEqual(int(out_100m.national_tax_annual_krw), 19_560_000)
        self.assertEqual(int(out_100m.local_tax_annual_krw), 1_956_000)

    def test_string_and_currency_input_are_normalized(self) -> None:
        out = compute_tax_official_core(taxable_income_annual_krw="20,000,000원", target_year=2026)
        self.assertTrue(bool(out.calculable))
        self.assertEqual(int(out.taxable_income_annual_krw), 20_000_000)
        self.assertEqual(int(out.total_tax_annual_krw), 1_914_000)

    def test_negative_input_is_blocked(self) -> None:
        out = compute_tax_official_core(taxable_income_annual_krw=-1, target_year=2026)
        self.assertFalse(bool(out.calculable))
        self.assertEqual(str(out.reason), "missing_taxable_income")


if __name__ == "__main__":
    unittest.main()
