from __future__ import annotations

import unittest

from services.nhis_estimator import estimate_nhis_current_vs_november, estimate_nhis_monthly_dict


class NhisOfficialGoldenTest(unittest.TestCase):
    def _steps(self, payload: dict) -> dict:
        out = estimate_nhis_monthly_dict(payload, snapshot_obj=None)
        return dict((out.get("basis") or {}).get("calc_steps") or {})

    def test_case_a_fin_9_9m_deposit_120m_floor_applied(self) -> None:
        out = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 4_950_000,
                "annual_dividend_krw": 4_950_000,
                "rent_deposit_krw": 120_000_000,
                "rent_monthly_krw": 0,
                "owned_home_rent_eval_krw": 0,
                "property_tax_base_total_krw": 0,
            },
            snapshot_obj=None,
        )
        steps = dict((out.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(steps.get("financial_income_included_krw") or 0), 0)
        self.assertEqual(int(steps.get("property_base_after_deduction_krw") or 0), 0)
        self.assertEqual(int(out.get("health_est_krw") or 0), 20_160)
        self.assertEqual(int(out.get("ltc_est_krw") or 0), 2_640)
        self.assertEqual(int(out.get("total_est_krw") or 0), 22_800)

    def test_case_b_fin_12m_deposit_120m_full_included(self) -> None:
        out = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_interest_krw": 12_000_000,
                "annual_dividend_krw": 0,
                "rent_deposit_krw": 120_000_000,
                "rent_monthly_krw": 0,
                "owned_home_rent_eval_krw": 0,
                "property_tax_base_total_krw": 0,
            },
            snapshot_obj=None,
        )
        steps = dict((out.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(steps.get("financial_income_included_krw") or 0), 12_000_000)
        self.assertEqual(int(steps.get("income_monthly_krw_used") or 0), 1_000_000)
        self.assertEqual(int(out.get("health_est_krw") or 0), 71_900)
        self.assertEqual(int(out.get("ltc_est_krw") or 0), 9_440)
        self.assertEqual(int(out.get("total_est_krw") or 0), 81_340)

    def test_case_c_income_monthly_floor_280k_rule(self) -> None:
        out = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_business_income_krw": 3_000_000,
            },
            snapshot_obj=None,
        )
        steps = dict((out.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(steps.get("income_monthly_krw_used") or 0), 280_000)
        self.assertEqual(int(steps.get("income_premium_step1_krw") or 0), 20_130)

    def test_case_d_cap_clamp(self) -> None:
        out = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_business_income_krw": 717_760_000,
                "property_tax_base_total_krw": 2_000_000_000_000,
            },
            snapshot_obj=None,
        )
        # 지역가입자 산식에서는 소득점수 상한(INCOME_POINT_CAP)으로 먼저 제한되어
        # health cap(4,591,740원)에 도달하지 않는다.
        steps = dict((out.get("basis") or {}).get("calc_steps") or {})
        self.assertEqual(int(out.get("health_est_krw") or 0), 4_579_750)
        self.assertEqual(int(steps.get("health_raw_krw") or 0), 4_579_750)
        self.assertFalse(bool(out.get("applied_cap")))

    def test_case_g_employee_cap_boundaries(self) -> None:
        # cap 직전
        before = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 127_725_730,
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(before.get("health_est_krw") or 0), 4_591_730)
        self.assertFalse(bool(before.get("applied_cap")))

        # cap 도달(raw == cap)
        exact = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 127_725_731,
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(exact.get("health_est_krw") or 0), 4_591_740)
        self.assertFalse(bool(exact.get("applied_cap")))

        # cap 초과(raw > cap)
        over = estimate_nhis_monthly_dict(
            {
                "member_type": "employee",
                "target_month": "2026-03",
                "salary_monthly_krw": 130_000_000,
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(over.get("health_est_krw") or 0), 4_591_740)
        self.assertTrue(bool(over.get("applied_cap")))

    def test_case_e_income_year_switch_by_november(self) -> None:
        march = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
            },
            snapshot_obj=None,
        )
        november = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-11",
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(march.get("income_year_applied") or 0), 2024)
        self.assertEqual(int(november.get("income_year_applied") or 0), 2025)

        compare = estimate_nhis_current_vs_november(
            {
                "member_type": "regional",
                "target_month": "2026-03",
            },
            snapshot_obj=None,
        )
        current_cycle = dict(compare.get("current_cycle") or {})
        november_cycle = dict(compare.get("november_cycle") or {})
        self.assertEqual(int(current_cycle.get("income_year_applied") or 0), 2024)
        self.assertEqual(int(november_cycle.get("income_year_applied") or 0), 2025)

    def test_case_f_property_after_deduction_zero(self) -> None:
        steps = self._steps(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "rent_deposit_krw": 30_000_000,
                "rent_monthly_krw": 0,
                "property_tax_base_total_krw": 0,
                "owned_home_rent_eval_krw": 0,
            }
        )
        self.assertEqual(int(steps.get("property_base_after_deduction_krw") or 0), 0)
        self.assertEqual(float(steps.get("property_points_step2") or 0.0), 0.0)
        self.assertEqual(int(steps.get("property_premium_step3_krw") or 0), 0)


if __name__ == "__main__":
    unittest.main()
