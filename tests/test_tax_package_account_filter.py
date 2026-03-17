from __future__ import annotations

import unittest

from services.tax_package import _normalize_account_filter


class TaxPackageAccountFilterTest(unittest.TestCase):
    def test_normalize_all_and_unassigned(self) -> None:
        self.assertEqual(_normalize_account_filter("all", None), ("all", 0))
        self.assertEqual(_normalize_account_filter("unassigned", None), ("unassigned", 0))

    def test_normalize_specific_account(self) -> None:
        self.assertEqual(_normalize_account_filter("5", None), ("5", 5))
        self.assertEqual(_normalize_account_filter("bad", 7), ("7", 7))

    def test_invalid_value_falls_back_to_all(self) -> None:
        self.assertEqual(_normalize_account_filter("bad", None), ("all", 0))


if __name__ == "__main__":
    unittest.main()
