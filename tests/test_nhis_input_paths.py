from __future__ import annotations

import unittest

from services.nhis_estimator import estimate_nhis_monthly_dict
from services.nhis_runtime import build_nhis_result_meta


class NhisInputPathsTest(unittest.TestCase):
    def _meta(self, estimate: dict, profile: dict | None = None) -> dict:
        return build_nhis_result_meta(
            estimate=estimate,
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile=profile or {},
        )

    def test_regional_with_core_inputs_is_normal(self) -> None:
        est = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_business_income_krw": 36_000_000,
                "property_tax_base_total_krw": 260_000_000,
            },
            snapshot_obj=None,
        )
        meta = self._meta(
            {**est, "confidence_level": "medium"},
            profile={
                "member_type": "regional",
                "annual_income_krw": 36_000_000,
                "non_salary_annual_income_krw": 0,
                "property_tax_base_total_krw": 260_000_000,
            },
        )
        self.assertTrue(bool(est.get("can_estimate")))
        self.assertEqual(str(meta.get("level") or ""), "normal")

    def test_employee_with_salary_is_normal(self) -> None:
        est = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 3_200_000,
            },
            snapshot_obj=None,
        )
        meta = self._meta(
            {**est, "confidence_level": "medium"},
            profile={
                "member_type": "employee",
                "salary_monthly_krw": 3_200_000,
                "non_salary_annual_income_krw": 0,
            },
        )
        self.assertEqual(str(est.get("mode") or ""), "employee")
        self.assertEqual(str(meta.get("level") or ""), "normal")

    def test_dependent_is_normal_with_zero(self) -> None:
        est = estimate_nhis_monthly_dict(
            {
                "member_type": "dependent",
                "target_month": "2026-03",
            },
            snapshot_obj=None,
        )
        meta = self._meta(est, profile={"member_type": "dependent"})
        self.assertEqual(int(est.get("total_est_krw") or 0), 0)
        self.assertEqual(str(meta.get("level") or ""), "normal")

    def test_employee_without_salary_falls_back_to_limited(self) -> None:
        est = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "annual_income_krw": 36_000_000,
            },
            snapshot_obj=None,
        )
        meta = self._meta(est, profile={"member_type": "employee", "annual_income_krw": 36_000_000})
        self.assertEqual(str(est.get("mode") or ""), "employee_income_proxy")
        self.assertEqual(str(est.get("confidence_level") or ""), "low")
        self.assertEqual(str(meta.get("level") or ""), "limited")

    def test_unknown_member_type_is_limited(self) -> None:
        est = estimate_nhis_monthly_dict(
            {
                "member_type": "unknown",
                "target_month": "2026-03",
            },
            snapshot_obj=None,
        )
        meta = self._meta(est, profile={"member_type": "unknown"})
        self.assertFalse(bool(est.get("can_estimate")))
        self.assertEqual(str(meta.get("level") or ""), "blocked")

    def test_financial_income_threshold_boundary(self) -> None:
        below = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 5_000_000,
                "annual_dividend_krw": 5_000_000,
            },
            snapshot_obj=None,
        )
        above = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 5_000_000,
                "annual_dividend_krw": 5_000_001,
            },
            snapshot_obj=None,
        )
        below_steps = dict((below.get("basis") or {}).get("calc_steps") or {})
        above_steps = dict((above.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(below_steps.get("financial_income_included_krw") or 0), 0)
        self.assertEqual(int(above_steps.get("financial_income_included_krw") or 0), 10_000_001)

    def test_property_deduction_boundary(self) -> None:
        below = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "property_tax_base_total_krw": 100_000_000,
            },
            snapshot_obj=None,
        )
        above = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "property_tax_base_total_krw": 100_000_001,
            },
            snapshot_obj=None,
        )
        below_steps = dict((below.get("basis") or {}).get("calc_steps") or {})
        above_steps = dict((above.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(below_steps.get("property_base_after_deduction_krw") or 0), 0)
        self.assertGreater(int(above_steps.get("property_base_after_deduction_krw") or 0), 0)


if __name__ == "__main__":
    unittest.main()
