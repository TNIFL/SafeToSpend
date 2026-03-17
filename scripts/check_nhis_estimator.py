from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# Allow "python scripts/check_nhis_estimator.py" from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.nhis_estimator import (
    INCOME_POINT_CAP,
    estimate_compare,
    estimate_nhis_current_vs_november,
    estimate_nhis_monthly_dict,
)
from services.nhis_rules import get_rules


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        effective_year=2026,
        health_insurance_rate=0.0719,
        long_term_care_ratio_of_health=0.1314,
        long_term_care_rate_optional=0.009448,
        regional_point_value=211.5,
        property_basic_deduction_krw=100_000_000,
        car_premium_enabled=False,
        income_reference_rule="1~10월은 전전년도, 11~12월은 전년도",
        fetched_at=None,
        sources_json={},
    )


def main() -> None:
    rules = get_rules("2026-03", None)
    assert abs(float(rules.health_insurance_rate) - 0.0719) < 0.0001
    assert abs(float(rules.regional_point_value) - 211.5) < 0.01
    assert abs(float(rules.ltc_ratio_of_health) - 0.1314) < 0.0001
    assert int(rules.property_basic_deduction_krw) == 100_000_000
    assert int(rules.monthly_floor_krw) == 20160
    assert int(rules.monthly_cap_krw) == 4_591_740

    snap = _snapshot()

    case_bill = {
        "member_type": "regional",
        "last_bill_score_points": 800,
    }
    case_simple = {
        "member_type": "regional",
        "annual_income_krw": 48_000_000,
        "property_tax_base_total_krw": 180_000_000,
        "rent_deposit_krw": 30_000_000,
        "rent_monthly_krw": 700_000,
        "target_month": "2026-03",
    }
    case_employee = {
        "member_type": "employee",
        "salary_monthly_krw": 3_500_000,
        "non_salary_annual_income_krw": 24_000_000,
    }
    case_employee_low = {
        "member_type": "employee",
        "salary_monthly_krw": 60_000,
    }

    for name, payload in [
        ("bill", case_bill),
        ("simple", case_simple),
        ("employee", case_employee),
        ("employee_low", case_employee_low),
    ]:
        out = estimate_nhis_monthly_dict(payload, snap)
        assert int(out["health_est_krw"]) >= 0
        assert int(out["ltc_est_krw"]) >= 0
        assert int(out["total_est_krw"]) >= 0
        assert "income_points" in out
        assert "property_points" in out
        assert "income_year_applied" in out
        assert "property_year_applied" in out
        if name == "simple":
            assert int(out["health_est_krw"]) >= 20160
        if name == "employee_low":
            assert bool(out["scale_warning"]) is True
            assert str(out["confidence_level"]) == "low"
        print(name, out["confidence_level"], out["total_est_krw"])

    # 소득점수 경계/상한 테스트(티켓4)
    income_zero = estimate_nhis_monthly_dict(
        {
            "member_type": "regional",
            "target_month": "2026-03",
            "annual_business_income_krw": 0,
        },
        snap,
    )
    income_mid = estimate_nhis_monthly_dict(
        {
            "member_type": "regional",
            "target_month": "2026-03",
            "annual_business_income_krw": 38_000_000,
        },
        snap,
    )
    income_cap = estimate_nhis_monthly_dict(
        {
            "member_type": "regional",
            "target_month": "2026-03",
            "annual_business_income_krw": 3_000_000_000,
        },
        snap,
    )
    assert float(income_zero["income_points"]) == 0.0
    assert float(income_mid["income_points"]) > 500.0
    assert float(income_cap["income_points"]) <= float(INCOME_POINT_CAP) + 0.001
    assert float(income_mid["income_points"]) < float(income_cap["income_points"])

    compare = estimate_nhis_current_vs_november(case_simple, snap)
    assert "current" in compare and "november" in compare
    assert "current_cycle" in compare and "november_cycle" in compare
    assert "nov_calc_reused_current" in compare and "fallback_used" in compare
    assert "fallback_reasons" in compare
    assert "scale_warning_current" in compare and "scale_warning_november" in compare
    assert "zero_diff_reason" in compare
    print("compare", compare["current_total_krw"], compare["november_total_krw"], compare["diff_krw"])

    direct_compare = estimate_compare(current_month="2026-03", profile=case_simple, snapshot_obj=snap)
    assert int(direct_compare["current_total_krw"]) >= 0
    assert int(direct_compare["november_total_krw"]) >= 0


if __name__ == "__main__":
    main()
