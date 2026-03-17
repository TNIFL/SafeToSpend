from __future__ import annotations

import unittest

from services.nhis_estimator import estimate_nhis_monthly_dict


def _accuracy_percent(*, expected: int, actual: int) -> float:
    exp = int(max(0, expected))
    act = int(max(0, actual))
    if exp <= 0:
        return 100.0 if act <= 0 else 0.0
    diff = abs(exp - act)
    return max(0.0, 100.0 - ((diff / exp) * 100.0))


def _band(accuracy_pct: float) -> str:
    if accuracy_pct >= 99.0:
        return "99%+"
    if accuracy_pct >= 95.0:
        return "95~99%"
    return "<95%"


class NhisAccuracyCasesTest(unittest.TestCase):
    def test_nhis_accuracy_case_table(self) -> None:
        cases = [
            {
                "id": "NHIS-ACC-01",
                "profile": {
                    "member_type": "regional",
                    "target_month": "2026-03",
                    "annual_business_income_krw": 12_000_000,
                },
                "expected_total_krw": 81_340,
                "expected_band": "99%+",
            },
            {
                "id": "NHIS-ACC-02",
                "profile": {
                    "member_type": "regional",
                    "target_month": "2026-03",
                    "annual_interest_krw": 4_950_000,
                    "annual_dividend_krw": 4_950_000,
                    "rent_deposit_krw": 120_000_000,
                    "rent_monthly_krw": 0,
                    "property_tax_base_total_krw": 0,
                },
                "expected_total_krw": 22_800,
                "expected_band": "99%+",
            },
            {
                "id": "NHIS-ACC-03",
                "profile": {
                    "member_type": "employee",
                    "target_month": "2026-03",
                    "salary_monthly_krw": 3_000_000,
                },
                "expected_total_krw": 122_020,
                "expected_band": "99%+",
            },
            {
                "id": "NHIS-ACC-04",
                "profile": {
                    "member_type": "employee",
                    "target_month": "2026-03",
                    "annual_income_krw": 36_000_000,
                },
                # 실제 월 보수가 3,100,000원이라고 가정한 공식값과 비교
                "expected_total_krw": 126_080,
                "expected_band": "95~99%",
            },
            {
                "id": "NHIS-ACC-05",
                "profile": {
                    "member_type": "dependent",
                    "target_month": "2026-03",
                },
                "expected_total_krw": 0,
                "expected_band": "99%+",
            },
            {
                "id": "NHIS-ACC-06",
                "profile": {
                    "member_type": "unknown",
                    "target_month": "2026-03",
                },
                # 숨은 실제값이 직장가입자 300만원 급여인 경우를 가정한 오차 시나리오
                "expected_total_krw": 122_020,
                "expected_band": "<95%",
            },
        ]

        for case in cases:
            with self.subTest(case=case["id"]):
                out = estimate_nhis_monthly_dict(dict(case["profile"]), snapshot_obj=None)
                actual = int(out.get("total_est_krw") or 0)
                expected = int(case["expected_total_krw"])
                accuracy_pct = _accuracy_percent(expected=expected, actual=actual)
                self.assertEqual(_band(accuracy_pct), str(case["expected_band"]))

    def test_year_boundary_uses_november_switch(self) -> None:
        march = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_business_income_krw": 12_000_000,
            },
            snapshot_obj=None,
        )
        november = estimate_nhis_monthly_dict(
            {
                "member_type": "regional",
                "target_month": "2026-11",
                "annual_business_income_krw": 12_000_000,
            },
            snapshot_obj=None,
        )
        self.assertEqual(int(march.get("income_year_applied") or 0), 2024)
        self.assertEqual(int(november.get("income_year_applied") or 0), 2025)


if __name__ == "__main__":
    unittest.main()
