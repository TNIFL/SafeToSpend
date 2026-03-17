from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class NhisReferenceSnapshot:
    effective_year: int
    effective_from_date: str
    last_checked_date: str
    health_insurance_rate: Decimal
    property_point_value: Decimal
    ltc_rate_of_income: Decimal
    ltc_ratio_of_health: Decimal
    premium_floor_health_only: int
    premium_ceiling_health_only: int
    property_basic_deduction_krw: int
    financial_income_threshold_krw: int
    financial_income_rule: str
    rent_month_to_deposit_multiplier: int
    rent_eval_multiplier: Decimal
    employee_share_ratio: Decimal
    car_premium_abolished: bool
    income_reference_rule: str
    sources: dict[str, tuple[str, ...]]

    def as_defaults_dict(self) -> dict[str, Any]:
        return {
            "effective_from_date": self.effective_from_date,
            "last_checked_date": self.last_checked_date,
            "health_insurance_rate": Decimal(self.health_insurance_rate),
            "regional_point_value": Decimal(self.property_point_value),
            "long_term_care_rate_optional": Decimal(self.ltc_rate_of_income),
            "long_term_care_ratio_of_health": Decimal(self.ltc_ratio_of_health),
            "monthly_floor_krw": int(self.premium_floor_health_only),
            "monthly_cap_krw": int(self.premium_ceiling_health_only),
            "property_basic_deduction_krw": int(self.property_basic_deduction_krw),
            "financial_income_threshold_krw": int(self.financial_income_threshold_krw),
            "financial_income_rule": str(self.financial_income_rule),
            "rent_month_to_deposit_multiplier": int(self.rent_month_to_deposit_multiplier),
            "rent_eval_multiplier": Decimal(self.rent_eval_multiplier),
            "car_premium_enabled": not bool(self.car_premium_abolished),
            "employee_share_ratio": Decimal(self.employee_share_ratio),
            "income_reference_rule": str(self.income_reference_rule),
            "sources": {k: tuple(v) for k, v in (self.sources or {}).items()},
        }


NHIS_REFERENCE_BY_YEAR: dict[int, NhisReferenceSnapshot] = {
    2026: NhisReferenceSnapshot(
        effective_year=2026,
        effective_from_date="2026-01-01",
        last_checked_date="2026-03-06",
        health_insurance_rate=Decimal("0.0719"),
        property_point_value=Decimal("211.5"),
        ltc_rate_of_income=Decimal("0.009448"),
        ltc_ratio_of_health=Decimal("0.1314"),
        premium_floor_health_only=20_160,
        premium_ceiling_health_only=4_591_740,
        property_basic_deduction_krw=100_000_000,
        financial_income_threshold_krw=10_000_000,
        financial_income_rule="이자+배당 합이 1,000만원 이하이면 제외, 1,000만원 초과 시 전액 합산",
        rent_month_to_deposit_multiplier=40,
        rent_eval_multiplier=Decimal("0.30"),
        employee_share_ratio=Decimal("0.5"),
        car_premium_abolished=True,
        income_reference_rule="1~10월은 전전년도 소득, 11~12월은 전년도 소득을 반영합니다.",
        sources={
            "health_rate_and_point_value": (
                "https://www.nhis.or.kr/lm/lmxsrv/law/lawLinkContentView.do?LINKCODE=c004400000&SEQ=28",
            ),
            "ltc_rate": (
                "https://www.mohw.go.kr/board.es?act=view&bid=0027&list_no=1487817&mid=a10503010200",
            ),
            "premium_floor_ceiling": (
                "https://www.law.go.kr/LSW//admRulInfoP.do?admRulSeq=2100000270472&chrClsCd=010201",
            ),
            "income_cycle_reference": (
                "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=69493",
            ),
            "financial_income_rule": (
                "https://www.nhis.or.kr/lm/lmxsrv/law/joHistoryContent.do?DATE_END=20240513&DATE_START=20240801&SEQ=29&SEQ_CONTENTS=4114846",
            ),
            "rent_eval_rule": (
                "https://www.law.go.kr/LSW/flDownload.do?bylClsCd=110201&flSeq=160135099&gubun=",
            ),
            "reform_2024_02": (
                "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9",
                "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B5%AD%EB%AF%BC%EA%B1%B4%EA%B0%95%EB%B3%B4%ED%97%98%EB%B2%95%EC%8B%9C%ED%96%89%EA%B7%9C%EC%B9%99",
            ),
        },
    ),
}


def resolve_nhis_reference_year(target_year: int) -> int:
    years = sorted(int(y) for y in NHIS_REFERENCE_BY_YEAR.keys())
    if not years:
        return int(target_year)
    chosen = years[0]
    for y in years:
        if y <= int(target_year):
            chosen = y
    return int(chosen)


def get_nhis_reference_snapshot(target_year: int) -> NhisReferenceSnapshot:
    year = resolve_nhis_reference_year(int(target_year))
    base = NHIS_REFERENCE_BY_YEAR.get(year)
    if base is None:
        raise KeyError(f"NHIS reference snapshot missing for year={target_year}")
    # target_year가 테이블 범위를 벗어나도 현재 스냅샷 값을 사용하되,
    # 화면 표기는 요청 연도를 기준으로 맞춘다.
    return replace(base, effective_year=int(target_year))


def diff_runtime_snapshot_vs_reference(
    *,
    effective_year: int,
    health_insurance_rate: Any,
    long_term_care_ratio_of_health: Any,
    regional_point_value: Any,
    property_basic_deduction_krw: Any,
    float_tolerance: float = 1e-6,
) -> list[str]:
    """런타임 snapshot 핵심값과 공식 reference 스냅샷의 불일치 필드를 반환한다."""

    ref = get_nhis_reference_snapshot(int(effective_year))
    mismatches: list[str] = []

    def _as_float(raw: Any) -> float:
        try:
            return float(raw)
        except Exception:
            return 0.0

    def _as_int(raw: Any) -> int:
        try:
            return int(raw)
        except Exception:
            return 0

    if abs(_as_float(health_insurance_rate) - float(ref.health_insurance_rate)) > float(float_tolerance):
        mismatches.append("health_insurance_rate")
    if abs(_as_float(long_term_care_ratio_of_health) - float(ref.ltc_ratio_of_health)) > float(float_tolerance):
        mismatches.append("long_term_care_ratio_of_health")
    if abs(_as_float(regional_point_value) - float(ref.property_point_value)) > float(float_tolerance):
        mismatches.append("regional_point_value")
    if _as_int(property_basic_deduction_krw) != int(ref.property_basic_deduction_krw):
        mismatches.append("property_basic_deduction_krw")

    return mismatches


def evaluate_rent_asset_value_krw(*, deposit_krw: int, monthly_krw: int, target_year: int) -> int:
    ref = get_nhis_reference_snapshot(int(target_year))
    deposit = max(0, int(deposit_krw or 0))
    monthly = max(0, int(monthly_krw or 0))
    base = deposit + (monthly * int(ref.rent_month_to_deposit_multiplier))
    value = Decimal(base) * Decimal(ref.rent_eval_multiplier)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def evaluate_financial_income_for_nhis(*, interest_krw: int, dividend_krw: int, target_year: int) -> int:
    ref = get_nhis_reference_snapshot(int(target_year))
    total = max(0, int(interest_krw or 0)) + max(0, int(dividend_krw or 0))
    # 시행규칙 제44조 단서: 1,000만원 이하 제외, 초과 시 전액 합산
    if total <= int(ref.financial_income_threshold_krw):
        return 0
    return int(total)


def resolve_income_applied_year(*, target_year: int, target_month: int) -> int:
    year = int(target_year)
    month = int(target_month)
    if 1 <= month <= 10:
        return year - 2
    return year - 1


def resolve_property_applied_year(*, target_year: int, target_month: int) -> int:
    year = int(target_year)
    month = int(target_month)
    if 1 <= month <= 10:
        return year - 1
    return year
