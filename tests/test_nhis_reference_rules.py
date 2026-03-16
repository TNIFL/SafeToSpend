from __future__ import annotations

import unittest

from services.nhis_estimator import estimate_nhis_monthly_dict
from services.nhis_rules import get_rules, month_cycle_info
from services.reference.nhis_reference import (
    diff_runtime_snapshot_vs_reference,
    evaluate_financial_income_for_nhis,
    evaluate_rent_asset_value_krw,
    get_nhis_reference_snapshot,
    resolve_income_applied_year,
)


class NhisReferenceRulesTest(unittest.TestCase):
    def test_reference_constants_exact(self) -> None:
        ref = get_nhis_reference_snapshot(2026)
        self.assertEqual(float(ref.health_insurance_rate), 0.0719)
        self.assertEqual(float(ref.property_point_value), 211.5)
        self.assertEqual(float(ref.ltc_rate_of_income), 0.009448)
        self.assertEqual(float(ref.ltc_ratio_of_health), 0.1314)
        self.assertEqual(int(ref.premium_floor_health_only), 20_160)
        self.assertEqual(int(ref.premium_ceiling_health_only), 4_591_740)
        self.assertEqual(int(ref.property_basic_deduction_krw), 100_000_000)
        self.assertTrue(bool(ref.car_premium_abolished))

    def test_rules_constants_exact(self) -> None:
        rules = get_rules("2026-03", snapshot_obj=None)
        self.assertEqual(float(rules.health_insurance_rate), 0.0719)
        self.assertEqual(float(rules.regional_point_value), 211.5)
        self.assertEqual(int(rules.health_premium_floor_krw), 20_160)
        self.assertEqual(int(rules.health_premium_cap_krw), 4_591_740)
        self.assertEqual(int(rules.property_basic_deduction_krw), 100_000_000)

    def test_rent_eval_formula_exact(self) -> None:
        # [보증금 + (월세 * 40)] * 0.30
        self.assertEqual(
            evaluate_rent_asset_value_krw(deposit_krw=0, monthly_krw=100_000, target_year=2026),
            1_200_000,
        )

    def test_financial_income_threshold_branch(self) -> None:
        self.assertEqual(
            evaluate_financial_income_for_nhis(interest_krw=5_000_000, dividend_krw=5_000_000, target_year=2026),
            0,
        )
        self.assertEqual(
            evaluate_financial_income_for_nhis(interest_krw=5_000_000, dividend_krw=5_000_001, target_year=2026),
            10_000_001,
        )

        est_le = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 5_000_000,
                "annual_dividend_krw": 5_000_000,
            },
            snapshot_obj=None,
        )
        est_gt = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 5_000_000,
                "annual_dividend_krw": 5_000_001,
            },
            snapshot_obj=None,
        )
        steps_le = dict((est_le.get("basis") or {}).get("calc_steps") or {})
        steps_gt = dict((est_gt.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(steps_le.get("financial_income_included_krw") or 0), 0)
        self.assertEqual(int(steps_gt.get("financial_income_included_krw") or 0), 10_000_001)

    def test_income_cycle_reference_exact(self) -> None:
        # 법령 기준: 1~10월 전전년도, 11~12월 전년도
        self.assertEqual(resolve_income_applied_year(target_year=2026, target_month=3), 2024)
        self.assertEqual(resolve_income_applied_year(target_year=2026, target_month=10), 2024)
        self.assertEqual(resolve_income_applied_year(target_year=2026, target_month=11), 2025)
        self.assertEqual(resolve_income_applied_year(target_year=2026, target_month=12), 2025)
        self.assertEqual(int(month_cycle_info("2026-03")["income_year_applied"]), 2024)
        self.assertEqual(int(month_cycle_info("2026-11")["income_year_applied"]), 2025)

    def test_employee_case_exact(self) -> None:
        out = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 3_000_000,
                "non_salary_annual_income_krw": 0,
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(out.get("health_est_krw") or 0), 107_850)
        # 10원 미만 절사 기준: 107,850 * 0.1314 = 14,171.49 -> 14,170
        self.assertEqual(int(out.get("ltc_est_krw") or 0), 14_170)

    def test_employee_health_cap_boundaries(self) -> None:
        before = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 127_725_730,
            },
            snapshot_obj=None,
        )
        before_steps = dict((before.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(before_steps.get("health_raw_krw") or 0), 4_591_730)
        self.assertEqual(int(before.get("health_est_krw") or 0), 4_591_730)
        self.assertFalse(bool(before.get("applied_cap")))

        at_cap = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 127_725_731,
            },
            snapshot_obj=None,
        )
        at_cap_steps = dict((at_cap.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(at_cap_steps.get("health_raw_krw") or 0), 4_591_740)
        self.assertEqual(int(at_cap.get("health_est_krw") or 0), 4_591_740)
        self.assertFalse(bool(at_cap.get("applied_cap")))

        over_cap = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 130_000_000,
            },
            snapshot_obj=None,
        )
        over_cap_steps = dict((over_cap.get("basis") or {}).get("calc_steps") or {})
        self.assertGreater(int(over_cap_steps.get("health_raw_krw") or 0), 4_591_740)
        self.assertEqual(int(over_cap.get("health_est_krw") or 0), 4_591_740)
        self.assertTrue(bool(over_cap.get("applied_cap")))

    def test_snapshot_reference_diff_helper(self) -> None:
        ok = diff_runtime_snapshot_vs_reference(
            effective_year=2026,
            health_insurance_rate=0.0719,
            long_term_care_ratio_of_health=0.1314,
            regional_point_value=211.5,
            property_basic_deduction_krw=100_000_000,
        )
        self.assertEqual(ok, [])

        mismatched = diff_runtime_snapshot_vs_reference(
            effective_year=2026,
            health_insurance_rate=0.07,
            long_term_care_ratio_of_health=0.1314,
            regional_point_value=211.5,
            property_basic_deduction_krw=99_000_000,
        )
        self.assertIn("health_insurance_rate", mismatched)
        self.assertIn("property_basic_deduction_krw", mismatched)


if __name__ == "__main__":
    unittest.main()
