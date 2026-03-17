#!/usr/bin/env python3
from __future__ import annotations

import sys

from services.nhis_estimator import estimate_nhis_monthly_dict


def _safe_int(raw: object) -> int:
    try:
        return int(raw or 0)
    except Exception:
        return 0


def main() -> int:
    profile = {
        "member_type": "regional",
        "target_month": "2026-03",
        "household_has_others": False,
        "non_salary_annual_income_krw": 12_000_000,
        "property_tax_base_total_krw": 0,
        "rent_deposit_krw": 120_000_000,
        "rent_monthly_krw": 0,
        "owned_home_rent_eval_krw": 0,
        "car_standard_value_krw": 0,
    }
    out = estimate_nhis_monthly_dict(profile=profile, snapshot_obj=None)
    basis = dict(out.get("basis") or {})
    calc = dict(basis.get("calc_steps") or {})

    income_monthly = _safe_int(calc.get("income_monthly_krw_used") or out.get("income_monthly_evaluated_krw"))
    income_premium = _safe_int(calc.get("income_premium_step1_krw") or out.get("income_premium_krw"))
    rent_eval = _safe_int(calc.get("rent_eval_krw"))
    deduction = _safe_int(calc.get("property_deduction_krw"))
    property_base_after_deduction = _safe_int(
        calc.get("property_base_after_deduction_krw") or calc.get("net_property_krw")
    )
    property_points = float(calc.get("property_points_step2") or out.get("property_points") or 0.0)
    point_value_used = float(calc.get("point_value_used") or out.get("point_value_used") or 0.0)
    property_premium = _safe_int(calc.get("property_premium_step3_krw") or out.get("property_premium_krw"))
    health = _safe_int(calc.get("health_premium_step4_krw") or out.get("health_est_krw"))
    ltc = _safe_int(calc.get("ltc_premium_step5_krw") or out.get("ltc_est_krw"))
    total = _safe_int(calc.get("total_premium_step6_krw") or out.get("total_est_krw"))

    unit_scale_warning = bool(calc.get("unit_scale_warning") or out.get("scale_warning"))
    duplication_suspected = bool(calc.get("duplication_suspected"))

    print("[NHIS SANITY] fixed-case: deposit=120,000,000 / monthly=0 / non-salary=12,000,000 / car=0 / dependents=0")
    print(f"① 소득월액(원): {income_monthly}")
    print(f"① 소득월액보험료(원): {income_premium}")
    print(f"전월세 평가액(원): {rent_eval} = (보증금 + 월세*40) * 0.30")
    print(f"재산 공제(원): {deduction}")
    print(f"공제 후 재산 기준액(원): {property_base_after_deduction}")
    print(f"② 재산보험료부과점수(점): {property_points}")
    print(f"③ 재산보험료(원): {property_premium} = ② * {point_value_used}")
    print(f"④ 건강보험료(원): {health}")
    print(f"⑤ 장기요양(원): {ltc}")
    print(f"⑥ 최종(원): {total}")
    print(f"flags.unit_scale_warning={str(unit_scale_warning).lower()}")
    print(f"flags.duplication_suspected={str(duplication_suspected).lower()}")

    if total < 70_000 or total > 150_000:
        print(f"FAIL: total_krw out of expected range (70,000~150,000): {total}")
        return 1
    if unit_scale_warning or duplication_suspected:
        print("WARN: flags are raised; please inspect ①~⑥ intermediate values.")
    print("PASS: total_krw is within expected guard range.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
