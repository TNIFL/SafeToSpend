from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.tax_package import _resolve_tax_buffer_metrics


class TaxPackageTaxMetricsTest(unittest.TestCase):
    def test_package_uses_same_tax_estimate_semantics_as_dashboard(self) -> None:
        est = SimpleNamespace(
            tax_rate=0.15,
            buffer_total_krw=120_000,
            buffer_target_krw=310_000,
            buffer_shortage_krw=190_000,
            tax_due_est_krw=310_000,
            tax_calculation_mode="official_exact",
            official_calculable=True,
            is_limited_estimate=False,
            official_block_reason="",
            taxable_income_input_source="profile_taxable_income",
        )
        with patch("services.risk.compute_tax_estimate", return_value=est):
            out = _resolve_tax_buffer_metrics(
                user_pk=5,
                month_key="2026-03",
                fallback_income_included_total=2_000_000,
            )

        self.assertEqual(float(out.get("tax_rate") or 0.0), 0.15)
        self.assertEqual(int(out.get("tax_due_est_krw") or 0), 310_000)
        self.assertEqual(str(out.get("tax_calculation_mode") or ""), "official_exact")
        self.assertTrue(bool(out.get("official_calculable")))
        self.assertFalse(bool(out.get("is_limited_estimate")))
        self.assertEqual(str(out.get("taxable_income_input_source") or ""), "profile_taxable_income")

    def test_package_fallback_path_is_marked_as_legacy(self) -> None:
        settings = SimpleNamespace(default_tax_rate=0.2)
        with (
            patch("services.risk.compute_tax_estimate", side_effect=RuntimeError("boom")),
            patch("services.tax_package._ensure_settings", return_value=settings),
            patch("services.tax_package.db.session.query") as query_mock,
        ):
            query_mock.return_value.filter.return_value.scalar.return_value = 50_000
            out = _resolve_tax_buffer_metrics(
                user_pk=9,
                month_key="2026-03",
                fallback_income_included_total=2_000_000,
            )

        self.assertEqual(str(out.get("tax_calculation_mode") or ""), "legacy_rate_fallback")
        self.assertFalse(bool(out.get("official_calculable")))
        self.assertTrue(bool(out.get("is_limited_estimate")))
        self.assertEqual(str(out.get("official_block_reason") or ""), "tax_estimate_unavailable")
        self.assertEqual(int(out.get("tax_due_est_krw") or 0), 400_000)
        self.assertEqual(int(out.get("tax_buffer_shortage") or 0), 350_000)


if __name__ == "__main__":
    unittest.main()
