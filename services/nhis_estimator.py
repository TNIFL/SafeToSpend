from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.nhis_rules import (
    NhisRules,
    get_rules,
    get_rules_for_month,
    month_cycle_info,
    parse_month_key,
    property_points_from_amount,
)

MAX_REASONABLE_MONTHLY_KRW = 20_000_000
INCOME_CAP_ANNUAL_KRW = 717_760_000
INCOME_POINT_CAP = 20_348.90
MIN_POINT_EXPECTED_WITH_INPUT = 100.0
MIN_REASONABLE_MONTHLY_WITH_INPUT = 5_000


@dataclass(frozen=True)
class NhisEstimate:
    member_type: str
    mode: str
    confidence_level: str  # high / medium / low
    can_estimate: bool

    health_est_krw: int
    ltc_est_krw: int
    total_est_krw: int

    income_monthly_evaluated_krw: int
    income_points: float
    income_premium_krw: int

    property_amount_krw: int
    property_points: float
    property_premium_krw: int

    vehicle_points: float
    vehicle_premium_krw: int
    total_points: float

    health_premium_raw_krw: int
    caps_applied: tuple[str, ...]
    floors_applied: tuple[str, ...]
    applied_floor: bool
    applied_cap: bool
    point_value_used: float
    ltc_ratio_used: float
    scale_warning: bool

    income_year_applied: int
    property_year_applied: int
    cycle_start_year: int

    notes: tuple[str, ...]
    warnings: tuple[str, ...]
    basis: dict[str, Any]


@dataclass(frozen=True)
class _RegionalBase:
    can_estimate: bool
    evaluated_monthly_income_krw: int
    financial_income_total_krw: int
    financial_income_included_krw: int
    income_points: float
    income_premium_krw: int
    gross_property_krw: int
    property_deduction_krw: int
    net_property_krw: int
    property_points: float
    property_premium_krw: int
    rent_eval_krw: int
    owned_home_rent_eval_krw: int
    has_income_input: bool
    has_property_input: bool
    asset_match_uncertain: bool
    duplication_suspected: bool
    source_scale_warning: bool
    notes: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _BillOverride:
    mode: str
    confidence: str
    health_krw: int
    ltc_krw: int
    total_krw: int
    points: float
    note: str


def _safe_int(raw: Any) -> int:
    if raw is None:
        return 0
    try:
        return int(str(raw).replace(",", "").strip() or "0")
    except Exception:
        return 0


def _safe_float(raw: Any) -> float:
    if raw is None:
        return 0.0
    try:
        return float(str(raw).replace(",", "").strip() or "0")
    except Exception:
        return 0.0


def _has_any_positive(profile: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        if _safe_int(profile.get(key)) > 0:
            return True
    return False


def _has_income_input(profile: dict[str, Any]) -> bool:
    keys = (
        "income_business_annual_krw",
        "annual_business_income_krw",
        "income_interest_annual_krw",
        "annual_interest_krw",
        "income_dividend_annual_krw",
        "annual_dividend_krw",
        "financial_income_annual_krw",
        "income_other_annual_krw",
        "annual_other_krw",
        "income_salary_annual_krw",
        "annual_salary_krw",
        "income_pension_annual_krw",
        "annual_pension_krw",
        "salary_monthly_krw",
        "annual_income_krw",
        "non_salary_annual_income_krw",
    )
    return _has_any_positive(profile, keys)


def _has_property_input(profile: dict[str, Any]) -> bool:
    keys = (
        "property_tax_base_total_krw",
        "property_tax_base_override_krw",
        "rent_deposit_krw",
        "rent_monthly_krw",
        "owned_home_rent_eval_krw",
    )
    if _has_any_positive(profile, keys):
        return True
    owned_list = profile.get("owned_property_tax_base_krw_list")
    if isinstance(owned_list, (list, tuple)):
        return any(_safe_int(v) > 0 for v in owned_list)
    return False


def _is_point_value_scale_suspicious(point_value: float) -> bool:
    pv = float(point_value or 0.0)
    if pv <= 0:
        return True
    if pv < 10:
        return True
    if pv > 5_000:
        return True
    return False


def _clamp_non_negative(n: int) -> int:
    value = int(n if n > 0 else 0)
    if value > MAX_REASONABLE_MONTHLY_KRW:
        return MAX_REASONABLE_MONTHLY_KRW
    return value


def _round_krw(value: float | int) -> int:
    return _clamp_non_negative(int(round(float(value or 0))))


def _truncate_under_10_krw(value: float | int) -> int:
    won = _clamp_non_negative(int(float(value or 0)))
    return int((won // 10) * 10)


def _premium_krw(value: float | int) -> int:
    # 건보료/장기요양보험료는 10원 미만 절사 기준으로 계산한다.
    return _truncate_under_10_krw(value)


def _calc_ltc(health_krw: int, ratio: float) -> int:
    if health_krw <= 0 or ratio <= 0:
        return 0
    return _premium_krw(health_krw * ratio)


def _median_int(values: list[int]) -> int:
    nums = sorted([int(v) for v in values if int(v) > 0])
    if not nums:
        return 0
    n = len(nums)
    mid = n // 2
    if n % 2 == 1:
        return int(nums[mid])
    return int(round((nums[mid - 1] + nums[mid]) / 2))


def _income_eval_components(profile: dict[str, Any], rules: NhisRules) -> tuple[int, int, int, int, int]:
    # 50% 반영 소득군(먼저 계산해서 연간 총소득 fallback과의 중복을 피한다)
    half_groups = (
        ("annual_salary_krw", "income_salary_annual_krw"),
        ("annual_pension_krw", "income_pension_annual_krw"),
    )
    half_income = 0
    for aliases in half_groups:
        selected = 0
        for key in aliases:
            selected = max(selected, max(0, _safe_int(profile.get(key))))
        half_income += selected
    salary_monthly = max(0, _safe_int(profile.get("salary_monthly_krw")))
    if half_income <= 0 and salary_monthly > 0:
        half_income += salary_monthly * 12

    # 100% 반영 소득군
    business_income = max(
        max(0, _safe_int(profile.get("annual_business_income_krw"))),
        max(0, _safe_int(profile.get("income_business_annual_krw"))),
    )
    interest_income = max(
        max(0, _safe_int(profile.get("annual_interest_krw"))),
        max(0, _safe_int(profile.get("income_interest_annual_krw"))),
    )
    dividend_income = max(
        max(0, _safe_int(profile.get("annual_dividend_krw"))),
        max(0, _safe_int(profile.get("income_dividend_annual_krw"))),
    )
    financial_income_total_input = max(0, _safe_int(profile.get("financial_income_annual_krw")))
    other_income = max(
        max(0, _safe_int(profile.get("annual_other_krw"))),
        max(0, _safe_int(profile.get("income_other_annual_krw"))),
    )
    financial_total = max(0, int(interest_income + dividend_income))
    if financial_total <= 0 and financial_income_total_input > 0:
        financial_total = int(financial_income_total_input)
    threshold = max(0, int(rules.financial_income_threshold_krw or 0))
    # 시행규칙 제44조 단서:
    # 금융소득(이자+배당) 합이 1,000만원 이하면 제외, 1,000만원 초과 시 전액 합산
    financial_included = financial_total if financial_total > threshold else 0
    full_income = int(max(0, business_income + other_income + financial_included))

    annual_income_fallback = max(0, _safe_int(profile.get("annual_income_krw")))
    non_salary_income = max(0, _safe_int(profile.get("non_salary_annual_income_krw")))
    # 우선순위:
    # 1) 상세 소득(사업/이자/배당/기타) 합산값
    # 2) 비근로 총액(non_salary_annual_income_krw)
    # 3) 레거시 연소득 fallback(단, 급여/연금 정보가 없을 때만)
    # -> fallback/신규 필드 동시 입력 시 이중 합산을 막는다.
    if full_income <= 0:
        if non_salary_income > 0:
            full_income = non_salary_income
        elif annual_income_fallback > 0 and half_income <= 0:
            full_income = annual_income_fallback

    evaluated_annual = max(0, int(round(full_income + (half_income * 0.5))))
    return int(full_income), int(half_income), int(evaluated_annual), int(financial_total), int(financial_included)


def _compute_income_points(evaluated_annual_krw: int, rules: NhisRules) -> tuple[float, int, int, list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    annual_capped = max(0, int(evaluated_annual_krw or 0))
    if annual_capped > INCOME_CAP_ANNUAL_KRW:
        annual_capped = int(INCOME_CAP_ANNUAL_KRW)
        notes.append("소득 반영 상한(연) 기준으로 조정했어요.")
    evaluated_monthly = max(0, int(annual_capped // 12))
    if 0 < evaluated_monthly <= int(rules.income_min_monthly_krw or 0):
        evaluated_monthly = int(rules.income_min_monthly_krw or evaluated_monthly)
        notes.append("소득월액 하한(28만원) 기준으로 보정했어요.")

    if evaluated_monthly <= 0:
        return 0.0, 0, evaluated_monthly, notes, warnings

    # 지역가입자 소득분은 공단 구조(소득월액 x 보험료율)로 고정한다.
    premium = _premium_krw(evaluated_monthly * float(rules.insurance_rate or 0.0))
    points = float((premium / rules.point_value) if float(rules.point_value or 0.0) > 0 else 0.0)
    if points > INCOME_POINT_CAP:
        points = float(INCOME_POINT_CAP)
        premium = _premium_krw(points * float(rules.point_value or 0.0))
        notes.append("소득 점수 상한 기준으로 조정했어요.")
    if _is_point_value_scale_suspicious(float(rules.point_value or 0.0)):
        warnings.append("점수당 금액 단위(원/점수)를 확인해 주세요.")
    return float(points), int(premium), evaluated_monthly, notes, warnings


def _compute_property_points(profile: dict[str, Any], rules: NhisRules) -> tuple[int, int, int, float, int, int, int, bool, list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []

    owned_list = profile.get("owned_property_tax_base_krw_list")
    owned_from_list = 0
    if isinstance(owned_list, (list, tuple)):
        for v in owned_list:
            owned_from_list += max(0, _safe_int(v))

    owned_single = max(0, _safe_int(profile.get("property_tax_base_total_krw")))
    owned_tax_base = owned_from_list if owned_from_list > 0 else owned_single

    rent_deposit = max(0, _safe_int(profile.get("rent_deposit_krw")))
    rent_monthly = max(0, _safe_int(profile.get("rent_monthly_krw")))
    owned_home_rent_eval = max(0, _safe_int(profile.get("owned_home_rent_eval_krw")))
    rent_eval = _round_krw((rent_deposit + (rent_monthly * rules.rent_month_to_deposit_multiplier)) * rules.rent_eval_multiplier)

    gross_property = max(0, owned_tax_base + rent_eval + owned_home_rent_eval)
    deduction = max(0, int(rules.property_basic_deduction_krw or 0))

    manual_override = max(0, _safe_int(profile.get("property_tax_base_override_krw")))
    if manual_override > 0:
        gross_property = manual_override
        notes.append("사용자가 입력한 재산 과세표준 값으로 우선 계산했어요.")

    net_property = max(0, gross_property - deduction)
    points_raw = property_points_from_amount(net_property, rules)
    points = float(points_raw or 0.0)
    premium = _premium_krw(points * rules.point_value)

    if points_raw is None:
        warnings.append("재산 점수표를 찾지 못해 재산 추정 신뢰도가 낮아요.")
    if not rules.property_points_table_loaded:
        warnings.append("공식 재산 점수표를 불러오지 못했어요. 계산 정확도가 낮을 수 있어요.")

    if gross_property > 0 and points <= 0:
        warnings.append("재산 점수 계산이 0으로 나왔어요. 재산 입력값을 확인해 주세요.")

    overlap_status = str(profile.get("asset_rent_overlap_status") or "").strip().lower()
    duplication_suspected = bool(profile.get("asset_rent_overlap_unknown"))
    if not duplication_suspected and rent_eval > 0 and owned_home_rent_eval > 0 and overlap_status in {"", "unknown"}:
        duplication_suspected = True
    if duplication_suspected:
        warnings.append("현재 거주 전월세와 보유 주택 임대 정보가 일부 겹칠 수 있어요.")

    return (
        int(gross_property),
        int(deduction),
        int(net_property),
        float(points),
        int(premium),
        int(rent_eval),
        int(owned_home_rent_eval),
        bool(duplication_suspected),
        notes,
        warnings,
    )


def _regional_base(profile: dict[str, Any], rules: NhisRules) -> _RegionalBase:
    notes: list[str] = []
    warnings: list[str] = [
        "세대 합산 여부, 경감/감면 적용 여부에 따라 달라질 수 있어요.",
        "모든 값은 공단 기준 자료와 입력값 기반 추정치예요.",
    ]

    full_income, half_income, eval_annual, fin_total, fin_included = _income_eval_components(profile, rules)
    income_points, income_premium, income_monthly, income_notes, income_warn = _compute_income_points(eval_annual, rules)
    notes.extend(income_notes)
    warnings.extend(income_warn)
    if fin_total > 0 and fin_included <= 0:
        notes.append("금융소득(이자+배당) 합이 기준 이하라 소득 반영에서 제외했어요.")
    if fin_total > 0 and abs(fin_total - int(rules.financial_income_threshold_krw or 0)) <= 2_000_000:
        warnings.append("금융소득이 1,000만원 전후면 반영 방식이 달라질 수 있어요.")

    (
        gross_property,
        deduction,
        net_property,
        property_points,
        property_premium,
        rent_eval,
        owned_home_rent_eval,
        duplication_suspected,
        property_notes,
        property_warn,
    ) = _compute_property_points(profile, rules)
    notes.extend(property_notes)
    warnings.extend(property_warn)
    if rules.used_snapshot_fallback:
        warnings.append("기준 데이터 준비 중(추정)이라 기본값으로 계산했어요.")

    has_income_input = _has_income_input(profile)
    has_property_input = _has_property_input(profile)
    asset_match_uncertain = bool(
        profile.get("asset_rent_overlap_unknown")
        or profile.get("asset_property_unknown")
        or profile.get("asset_current_rent_unknown")
    )
    source_scale_warning = _is_point_value_scale_suspicious(float(rules.point_value))
    if source_scale_warning:
        warnings.append("점수당 금액 단위(원/점수)를 확인해 주세요.")
    if asset_match_uncertain:
        warnings.append("자산 매칭이 불확실해 중복 가능성을 보수적으로 처리했어요.")

    can_estimate = bool(
        eval_annual > 0
        or gross_property > 0
        or max(0, _safe_int(profile.get("last_bill_score_points"))) > 0
        or max(0, _safe_int(profile.get("last_bill_health_only_krw"))) > 0
        or max(0, _safe_int(profile.get("last_bill_total_krw"))) > 0
    )

    if not can_estimate:
        notes.append("소득 또는 재산 입력이 없어 추정 정확도가 낮아요.")

    if full_income <= 0 and half_income <= 0:
        warnings.append("소득 입력이 없으면 최소 보험료에 가깝게 계산될 수 있어요.")
    if gross_property <= 0:
        warnings.append("재산 입력이 없으면 재산분이 반영되지 않아요.")

    return _RegionalBase(
        can_estimate=can_estimate,
        evaluated_monthly_income_krw=int(income_monthly),
        financial_income_total_krw=int(fin_total),
        financial_income_included_krw=int(fin_included),
        income_points=float(income_points),
        income_premium_krw=int(income_premium),
        gross_property_krw=int(gross_property),
        property_deduction_krw=int(deduction),
        net_property_krw=int(net_property),
        property_points=float(property_points),
        property_premium_krw=int(property_premium),
        rent_eval_krw=int(rent_eval),
        owned_home_rent_eval_krw=int(owned_home_rent_eval),
        has_income_input=bool(has_income_input),
        has_property_input=bool(has_property_input),
        asset_match_uncertain=bool(asset_match_uncertain),
        duplication_suspected=bool(duplication_suspected),
        source_scale_warning=bool(source_scale_warning),
        notes=tuple(notes),
        warnings=tuple(dict.fromkeys([w for w in warnings if w])),
    )


def _estimate_from_bill(profile: dict[str, Any], rules: NhisRules, cycle: dict[str, int]) -> _BillOverride | None:
    ignore_last_bill_override = bool(profile.get("ignore_last_bill_override"))
    score_points = 0 if ignore_last_bill_override else max(0, _safe_int(profile.get("last_bill_score_points")))
    health_only = 0 if ignore_last_bill_override else max(0, _safe_int(profile.get("last_bill_health_only_krw")))
    total_bill = 0 if ignore_last_bill_override else max(0, _safe_int(profile.get("last_bill_total_krw")))

    if score_points > 0:
        health = _premium_krw(score_points * rules.point_value)
        ltc = _calc_ltc(health, rules.ltc_ratio_of_health)
        return _BillOverride(
            mode="bill_score",
            confidence="high",
            health_krw=health,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(health + ltc),
            points=float(score_points),
            note="고지서 부과점수 기준으로 계산했어요.",
        )

    if health_only > 0:
        ltc = _calc_ltc(health_only, rules.ltc_ratio_of_health)
        return _BillOverride(
            mode="bill_health",
            confidence="medium",
            health_krw=health_only,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(health_only + ltc),
            points=float((health_only / rules.point_value) if rules.point_value > 0 else 0.0),
            note="최근 고지서 건보료 금액 기준으로 계산했어요.",
        )

    if total_bill > 0:
        divisor = 1.0 + max(0.0, float(rules.ltc_ratio_of_health or 0.0))
        health = _premium_krw(total_bill / divisor) if divisor > 0 else _clamp_non_negative(total_bill)
        ltc = _clamp_non_negative(total_bill - health)
        return _BillOverride(
            mode="bill_total",
            confidence="medium",
            health_krw=health,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(health + ltc),
            points=float((health / rules.point_value) if rules.point_value > 0 else 0.0),
            note="최근 고지서 합계 기준으로 건보료/장기요양을 분해했어요.",
        )

    rows = profile.get("bill_history") or []
    if not isinstance(rows, list) or not rows:
        return None

    target_year = int(cycle.get("income_year_applied") or cycle.get("target_year") or 0)
    candidate_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        year = _safe_int(row.get("bill_year"))
        if year <= 0:
            continue
        if abs(year - target_year) <= 1:
            candidate_rows.append(row)
    if not candidate_rows:
        candidate_rows = [r for r in rows if isinstance(r, dict)]

    score_values: list[int] = []
    health_values: list[int] = []
    total_values: list[int] = []
    for row in candidate_rows:
        score = max(0, _safe_int(row.get("score_points")))
        if score > 0:
            score_values.append(score)
            continue
        health = max(0, _safe_int(row.get("health_only_krw")))
        if health > 0:
            health_values.append(health)
            continue
        total = max(0, _safe_int(row.get("total_krw")))
        if total > 0:
            total_values.append(total)

    if score_values:
        med_score = _median_int(score_values)
        health = _premium_krw(med_score * rules.point_value)
        ltc = _calc_ltc(health, rules.ltc_ratio_of_health)
        return _BillOverride(
            mode="bill_history_score",
            confidence=("high" if len(score_values) >= 2 else "medium"),
            health_krw=health,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(health + ltc),
            points=float(med_score),
            note=f"과거 고지서 점수 {len(score_values)}건 기준으로 보정했어요.",
        )

    if health_values:
        med_health = _median_int(health_values)
        ltc = _calc_ltc(med_health, rules.ltc_ratio_of_health)
        return _BillOverride(
            mode="bill_history_health",
            confidence=("medium" if len(health_values) >= 2 else "low"),
            health_krw=med_health,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(med_health + ltc),
            points=float((med_health / rules.point_value) if rules.point_value > 0 else 0.0),
            note=f"과거 고지서 건보료 {len(health_values)}건 기준으로 보정했어요.",
        )

    if total_values:
        med_total = _median_int(total_values)
        divisor = 1.0 + max(0.0, float(rules.ltc_ratio_of_health or 0.0))
        health = _premium_krw(med_total / divisor) if divisor > 0 else med_total
        ltc = _clamp_non_negative(med_total - health)
        return _BillOverride(
            mode="bill_history_total",
            confidence=("medium" if len(total_values) >= 2 else "low"),
            health_krw=health,
            ltc_krw=ltc,
            total_krw=_clamp_non_negative(health + ltc),
            points=float((health / rules.point_value) if rules.point_value > 0 else 0.0),
            note=f"과거 고지서 합계 {len(total_values)}건 기준으로 보정했어요.",
        )

    return None


def _estimate_regional(profile: dict[str, Any], rules: NhisRules, cycle: dict[str, int]) -> NhisEstimate:
    base = _regional_base(profile, rules)

    vehicle_points = 0.0
    vehicle_premium = 0

    health_raw = _clamp_non_negative(int(base.income_premium_krw + base.property_premium_krw + vehicle_premium))
    health = int(health_raw)
    caps_applied: list[str] = []
    floors_applied: list[str] = []

    if health > rules.health_premium_cap_krw:
        health = int(rules.health_premium_cap_krw)
        caps_applied.append("health_cap")

    if health < rules.health_premium_floor_krw:
        health = int(rules.health_premium_floor_krw)
        floors_applied.append("health_floor")

    applied_cap = bool("health_cap" in caps_applied)
    applied_floor = bool("health_floor" in floors_applied)

    # 고지서 데이터가 있으면 총액을 우선 보정
    bill_override = _estimate_from_bill(profile, rules, cycle)
    mode = "rules_regional"
    confidence = "low"
    notes = list(base.notes)
    warnings = list(base.warnings)
    scale_warning = bool(base.source_scale_warning)

    if bill_override is not None:
        mode = bill_override.mode
        confidence = bill_override.confidence
        health = _clamp_non_negative(int(bill_override.health_krw))
        if health > rules.health_premium_cap_krw:
            health = int(rules.health_premium_cap_krw)
            if "health_cap" not in caps_applied:
                caps_applied.append("health_cap")
        if health < rules.health_premium_floor_krw:
            health = int(rules.health_premium_floor_krw)
            if "health_floor" not in floors_applied:
                floors_applied.append("health_floor")
        applied_cap = bool("health_cap" in caps_applied)
        applied_floor = bool("health_floor" in floors_applied)
        ltc = _clamp_non_negative(int(bill_override.ltc_krw))
        total = _clamp_non_negative(int(bill_override.total_krw))
        if total <= 0:
            ltc = _calc_ltc(health, rules.ltc_ratio_of_health)
            total = _clamp_non_negative(health + ltc)
        notes.insert(0, bill_override.note)
        total_points = float(
            bill_override.points if bill_override.points > 0 else (health / rules.point_value if rules.point_value > 0 else 0.0)
        )
        health_raw = int(health)
    else:
        ltc = _calc_ltc(health, rules.ltc_ratio_of_health)
        total = _clamp_non_negative(health + ltc)
        total_points = float((health / rules.point_value) if rules.point_value > 0 else 0.0)
        confidence = "medium" if base.can_estimate else "low"

    if total_points < MIN_POINT_EXPECTED_WITH_INPUT and (base.has_income_input or base.has_property_input):
        warnings.append("점수가 낮아 입력 누락 가능성이 있어요. 소득/재산/고지서를 확인해 주세요.")
        scale_warning = True
        confidence = "low"

    if not base.source_scale_warning and _is_point_value_scale_suspicious(float(rules.point_value)):
        scale_warning = True
        warnings.append("점수당 금액 단위(원/점수)가 비정상으로 보입니다.")
    if base.asset_match_uncertain and bill_override is None:
        scale_warning = True
        confidence = "low"
    if base.duplication_suspected:
        confidence = "low"

    if not base.can_estimate and bill_override is None:
        notes.insert(0, "입력값이 부족해 최소 추정으로 계산했어요.")
    if rules.used_snapshot_fallback:
        confidence = "low"
    if not rules.property_points_table_loaded:
        confidence = "low"

    basis = {
        "source_name": "국민건강보험법 시행령/시행규칙 + 복지부 고시 기준",
        "source_year": int(rules.effective_year),
        "effective_date": rules.effective_date,
        "reference_last_checked_date": str(rules.reference_last_checked_date or "-"),
        "income_year_applied": int(cycle.get("income_year_applied") or 0),
        "property_year_applied": int(cycle.get("property_year_applied") or 0),
        "cycle_start_year": int(cycle.get("cycle_start_year") or 0),
        "fetched_at": rules.fetched_at_text,
        "matched_key": f"mode={mode}",
        "calc_steps": {
            "evaluated_monthly_income_krw": int(base.evaluated_monthly_income_krw),
            "income_points": round(float(base.income_points), 4),
            "income_premium_krw": int(base.income_premium_krw),
            "gross_property_krw": int(base.gross_property_krw),
            "property_deduction_krw": int(base.property_deduction_krw),
            "net_property_krw": int(base.net_property_krw),
            "property_points": round(float(base.property_points), 4),
            "property_premium_krw": int(base.property_premium_krw),
            "rent_eval_krw": int(base.rent_eval_krw),
            "owned_home_rent_eval_krw": int(base.owned_home_rent_eval_krw),
            "asset_match_uncertain": bool(base.asset_match_uncertain),
            "duplication_suspected": bool(base.duplication_suspected),
            "vehicle_points": round(float(vehicle_points), 4),
            "vehicle_premium_krw": int(vehicle_premium),
            "health_raw_krw": int(health_raw),
            "health_floor_krw": int(rules.health_premium_floor_krw),
            "health_cap_krw": int(rules.health_premium_cap_krw),
            "point_value_used": float(rules.point_value),
            "ltc_ratio_used": float(rules.ltc_ratio_of_health),
            "applied_floor": bool(applied_floor),
            "applied_cap": bool(applied_cap),
            "income_monthly_krw_used": int(base.evaluated_monthly_income_krw),
            "income_premium_step1_krw": int(base.income_premium_krw),
            "financial_income_total_krw": int(base.financial_income_total_krw),
            "financial_income_included_krw": int(base.financial_income_included_krw),
            "financial_income_threshold_krw": int(rules.financial_income_threshold_krw),
            "property_base_after_deduction_krw": int(base.net_property_krw),
            "property_points_step2": round(float(base.property_points), 4),
            "property_premium_step3_krw": int(base.property_premium_krw),
            "health_premium_step4_krw": int(health),
            "ltc_premium_step5_krw": int(ltc),
            "total_premium_step6_krw": int(total),
            "unit_scale_warning": bool(scale_warning),
        },
        "confidence": confidence,
        "note": "산식은 법령/고시 기준, 입력 데이터는 사용자 값 기반 추정이에요.",
        "source_urls": dict(rules.source_urls),
        "rules_version": rules.rules_version,
        "used_snapshot_fallback": bool(rules.used_snapshot_fallback),
    }

    return NhisEstimate(
        member_type="regional",
        mode=mode,
        confidence_level=confidence,
        can_estimate=bool(base.can_estimate or bill_override is not None),
        health_est_krw=int(health),
        ltc_est_krw=int(ltc),
        total_est_krw=int(total),
        income_monthly_evaluated_krw=int(base.evaluated_monthly_income_krw),
        income_points=float(base.income_points),
        income_premium_krw=int(base.income_premium_krw),
        property_amount_krw=int(base.net_property_krw),
        property_points=float(base.property_points),
        property_premium_krw=int(base.property_premium_krw),
        vehicle_points=float(vehicle_points),
        vehicle_premium_krw=int(vehicle_premium),
        total_points=float(max(0.0, total_points)),
        health_premium_raw_krw=int(health_raw),
        caps_applied=tuple(caps_applied),
        floors_applied=tuple(floors_applied),
        applied_floor=bool(applied_floor),
        applied_cap=bool(applied_cap),
        point_value_used=float(rules.point_value),
        ltc_ratio_used=float(rules.ltc_ratio_of_health),
        scale_warning=bool(scale_warning),
        income_year_applied=int(cycle.get("income_year_applied") or 0),
        property_year_applied=int(cycle.get("property_year_applied") or 0),
        cycle_start_year=int(cycle.get("cycle_start_year") or 0),
        notes=tuple(dict.fromkeys([n for n in notes if n])),
        warnings=tuple(dict.fromkeys([w for w in warnings if w])),
        basis=basis,
    )


def _estimate_employee(profile: dict[str, Any], rules: NhisRules, cycle: dict[str, int]) -> NhisEstimate:
    salary_monthly = max(0, _safe_int(profile.get("salary_monthly_krw")))
    annual_income_fallback = max(0, _safe_int(profile.get("annual_income_krw")))
    non_salary_annual = max(0, _safe_int(profile.get("non_salary_annual_income_krw")))

    notes: list[str] = []
    warnings: list[str] = []
    if rules.used_snapshot_fallback:
        warnings.append("기준 데이터 준비 중(추정)이라 기본값으로 계산했어요.")

    salary_from_annual_fallback = False
    if salary_monthly <= 0 and annual_income_fallback > 0:
        salary_monthly = int(max(0, round(annual_income_fallback / 12)))
        salary_from_annual_fallback = True
        notes.append("월 보수 입력이 없어 연소득을 12로 나눠 임시 반영했어요.")
        warnings.append("월 보수 입력을 넣으면 직장가입자 정확도가 더 올라가요.")

    if salary_monthly <= 0:
        warnings.append("월 보수(급여)를 입력하면 직장가입자 추정 정확도가 올라가요.")
        return NhisEstimate(
            member_type="employee",
            mode="insufficient",
            confidence_level="low",
            can_estimate=False,
            health_est_krw=0,
            ltc_est_krw=0,
            total_est_krw=0,
            income_monthly_evaluated_krw=0,
            income_points=0.0,
            income_premium_krw=0,
            property_amount_krw=0,
            property_points=0.0,
            property_premium_krw=0,
            vehicle_points=0.0,
            vehicle_premium_krw=0,
            total_points=0.0,
            health_premium_raw_krw=0,
            caps_applied=(),
            floors_applied=(),
            applied_floor=False,
            applied_cap=False,
            point_value_used=float(rules.point_value),
            ltc_ratio_used=float(rules.ltc_ratio_of_health),
            scale_warning=False,
            income_year_applied=int(cycle.get("income_year_applied") or 0),
            property_year_applied=int(cycle.get("property_year_applied") or 0),
            cycle_start_year=int(cycle.get("cycle_start_year") or 0),
            notes=("월 보수(급여) 입력이 필요해요.",),
            warnings=tuple(warnings),
            basis={
                "source_name": "직장가입자 월 보수 기준 단순 추정",
                "source_year": int(rules.effective_year),
                "effective_date": rules.effective_date,
                "reference_last_checked_date": str(rules.reference_last_checked_date or "-"),
                "confidence": "low",
                "source_urls": dict(rules.source_urls),
            },
        )

    employee_share = max(0.0, min(1.0, float(rules.employee_share_ratio or 0.5)))
    base_health = _premium_krw(salary_monthly * rules.insurance_rate * employee_share)
    extra_health = 0
    if non_salary_annual > 20_000_000:
        extra_monthly_income = (non_salary_annual - 20_000_000) / 12.0
        extra_health = _premium_krw(extra_monthly_income * rules.insurance_rate * employee_share)
        warnings.append("보수 외 소득이 연 2,000만원을 넘으면 추가 보험료가 생길 수 있어요.")
        notes.append("보수 외 소득월액보험료를 보수적으로 반영했어요.")

    health_raw = _clamp_non_negative(base_health + extra_health)
    health = min(int(health_raw), int(rules.health_premium_cap_krw))
    caps_applied: list[str] = []
    floors_applied: list[str] = []
    if health_raw > rules.health_premium_cap_krw:
        caps_applied.append("health_cap")
    if health < rules.health_premium_floor_krw:
        health = int(rules.health_premium_floor_krw)
        floors_applied.append("health_floor")

    ltc = _calc_ltc(health, rules.ltc_ratio_of_health)
    total = _clamp_non_negative(health + ltc)
    points = float((health / rules.point_value) if rules.point_value > 0 else 0.0)
    scale_warning = bool(_is_point_value_scale_suspicious(float(rules.point_value)))
    if scale_warning:
        warnings.append("점수당 금액 단위(원/점수)를 확인해 주세요.")

    if not notes:
        notes.append("월 보수 기준으로 계산했어요.")
    employee_confidence = "low" if rules.used_snapshot_fallback else "medium"
    if salary_from_annual_fallback:
        employee_confidence = "low"
    if salary_monthly > 0 and total > 0 and total < MIN_REASONABLE_MONTHLY_WITH_INPUT:
        scale_warning = True
        employee_confidence = "low"
        warnings.append("입력이 있는데 월 보험료가 매우 낮게 계산돼 단위/입력값 확인이 필요해요.")

    basis = {
        "source_name": "국민건강보험법 시행령 제44조 + 복지부 장기요양 비율",
        "source_year": int(rules.effective_year),
        "effective_date": rules.effective_date,
        "reference_last_checked_date": str(rules.reference_last_checked_date or "-"),
        "income_year_applied": int(cycle.get("income_year_applied") or 0),
        "property_year_applied": int(cycle.get("property_year_applied") or 0),
        "cycle_start_year": int(cycle.get("cycle_start_year") or 0),
        "matched_key": "employee_salary",
        "calc_steps": {
            "salary_monthly_krw": int(salary_monthly),
            "salary_from_annual_fallback": bool(salary_from_annual_fallback),
            "health_rate": float(rules.insurance_rate),
            "employee_share_ratio": float(employee_share),
            "base_health_krw": int(base_health),
            "extra_health_krw": int(extra_health),
            "health_raw_krw": int(health_raw),
            "health_cap_krw": int(rules.health_premium_cap_krw),
            "point_value_used": float(rules.point_value),
            "ltc_ratio_used": float(rules.ltc_ratio_of_health),
            "applied_floor": bool(floors_applied),
            "applied_cap": bool(caps_applied),
        },
        "confidence": employee_confidence,
        "note": "직장가입자 추정은 보수/보수외소득 입력값에 따라 달라져요.",
        "source_urls": dict(rules.source_urls),
        "rules_version": rules.rules_version,
        "used_snapshot_fallback": bool(rules.used_snapshot_fallback),
    }

    return NhisEstimate(
        member_type="employee",
        mode=("employee_income_proxy" if salary_from_annual_fallback else "employee"),
        confidence_level=employee_confidence,
        can_estimate=True,
        health_est_krw=int(health),
        ltc_est_krw=int(ltc),
        total_est_krw=int(total),
        income_monthly_evaluated_krw=int(salary_monthly),
        income_points=float(points),
        income_premium_krw=int(health),
        property_amount_krw=0,
        property_points=0.0,
        property_premium_krw=0,
        vehicle_points=0.0,
        vehicle_premium_krw=0,
        total_points=float(points),
        health_premium_raw_krw=int(health_raw),
        caps_applied=tuple(caps_applied),
        floors_applied=tuple(floors_applied),
        applied_floor=bool(floors_applied),
        applied_cap=bool(caps_applied),
        point_value_used=float(rules.point_value),
        ltc_ratio_used=float(rules.ltc_ratio_of_health),
        scale_warning=bool(scale_warning),
        income_year_applied=int(cycle.get("income_year_applied") or 0),
        property_year_applied=int(cycle.get("property_year_applied") or 0),
        cycle_start_year=int(cycle.get("cycle_start_year") or 0),
        notes=tuple(notes),
        warnings=tuple(warnings),
        basis=basis,
    )


def estimate_nhis_monthly(profile: dict[str, Any], snapshot_obj: Any) -> NhisEstimate:
    profile_local = dict(profile or {})
    target_month = parse_month_key(profile_local.get("target_month"))
    profile_local["target_month"] = target_month

    rules = get_rules(target_month, snapshot_obj=snapshot_obj)
    cycle = month_cycle_info(target_month)

    member_type = str(profile_local.get("member_type") or "unknown").strip().lower()
    if member_type not in {"regional", "employee", "dependent", "unknown"}:
        member_type = "unknown"

    if member_type == "dependent":
        return NhisEstimate(
            member_type="dependent",
            mode="dependent",
            confidence_level="medium",
            can_estimate=True,
            health_est_krw=0,
            ltc_est_krw=0,
            total_est_krw=0,
            income_monthly_evaluated_krw=0,
            income_points=0.0,
            income_premium_krw=0,
            property_amount_krw=0,
            property_points=0.0,
            property_premium_krw=0,
            vehicle_points=0.0,
            vehicle_premium_krw=0,
            total_points=0.0,
            health_premium_raw_krw=0,
            caps_applied=(),
            floors_applied=(),
            applied_floor=False,
            applied_cap=False,
            point_value_used=float(rules.point_value),
            ltc_ratio_used=float(rules.ltc_ratio_of_health),
            scale_warning=False,
            income_year_applied=int(cycle.get("income_year_applied") or 0),
            property_year_applied=int(cycle.get("property_year_applied") or 0),
            cycle_start_year=int(cycle.get("cycle_start_year") or 0),
            notes=("피부양자는 별도 납부가 없을 수 있어요.",),
            warnings=(),
            basis={
                "source_name": "피부양자 분류 기준",
                "source_year": int(rules.effective_year),
                "effective_date": rules.effective_date,
                "reference_last_checked_date": str(rules.reference_last_checked_date or "-"),
                "confidence": "medium",
                "source_urls": dict(rules.source_urls),
            },
        )

    if member_type == "employee":
        return _estimate_employee(profile_local, rules, cycle)

    if member_type == "regional":
        return _estimate_regional(profile_local, rules, cycle)

    return NhisEstimate(
        member_type="unknown",
        mode="insufficient",
        confidence_level="low",
        can_estimate=False,
        health_est_krw=0,
        ltc_est_krw=0,
        total_est_krw=0,
        income_monthly_evaluated_krw=0,
        income_points=0.0,
        income_premium_krw=0,
        property_amount_krw=0,
        property_points=0.0,
        property_premium_krw=0,
        vehicle_points=0.0,
        vehicle_premium_krw=0,
        total_points=0.0,
        health_premium_raw_krw=0,
        caps_applied=(),
        floors_applied=(),
        applied_floor=False,
        applied_cap=False,
        point_value_used=float(rules.point_value),
        ltc_ratio_used=float(rules.ltc_ratio_of_health),
        scale_warning=False,
        income_year_applied=int(cycle.get("income_year_applied") or 0),
        property_year_applied=int(cycle.get("property_year_applied") or 0),
        cycle_start_year=int(cycle.get("cycle_start_year") or 0),
        notes=("가입 유형을 선택하면 계산할 수 있어요.",),
        warnings=("고지서 금액을 입력하면 정확도가 크게 올라가요.",),
        basis={
            "source_name": "국민건강보험료 기준",
            "source_year": int(rules.effective_year),
            "effective_date": rules.effective_date,
            "reference_last_checked_date": str(rules.reference_last_checked_date or "-"),
            "confidence": "low",
            "source_urls": dict(rules.source_urls),
        },
    )


def nhis_estimate_to_dict(estimate: NhisEstimate) -> dict[str, Any]:
    return {
        "member_type": estimate.member_type,
        "mode": estimate.mode,
        "confidence_level": estimate.confidence_level,
        "can_estimate": bool(estimate.can_estimate),
        "health_est_krw": int(estimate.health_est_krw),
        "ltc_est_krw": int(estimate.ltc_est_krw),
        "total_est_krw": int(estimate.total_est_krw),
        "health_premium_krw": int(estimate.health_est_krw),
        "ltc_premium_krw": int(estimate.ltc_est_krw),
        "total_premium_krw": int(estimate.total_est_krw),
        "income_monthly_evaluated_krw": int(estimate.income_monthly_evaluated_krw),
        "income_points": float(estimate.income_points),
        "income_premium_krw": int(estimate.income_premium_krw),
        "property_amount_krw": int(estimate.property_amount_krw),
        "property_points": float(estimate.property_points),
        "property_premium_krw": int(estimate.property_premium_krw),
        "vehicle_points": float(estimate.vehicle_points),
        "vehicle_premium_krw": int(estimate.vehicle_premium_krw),
        "total_points": float(estimate.total_points),
        "health_premium_raw_krw": int(estimate.health_premium_raw_krw),
        "caps_applied": list(estimate.caps_applied),
        "floors_applied": list(estimate.floors_applied),
        "applied_floor": bool(estimate.applied_floor),
        "applied_cap": bool(estimate.applied_cap),
        "point_value_used": float(estimate.point_value_used),
        "ltc_ratio_used": float(estimate.ltc_ratio_used),
        "scale_warning": bool(estimate.scale_warning),
        "income_year_applied": int(estimate.income_year_applied),
        "property_year_applied": int(estimate.property_year_applied),
        "cycle_start_year": int(estimate.cycle_start_year),
        "notes": list(estimate.notes),
        "warnings": list(estimate.warnings),
        "basis": dict(estimate.basis or {}),
    }


def estimate_nhis_monthly_dict(profile: dict[str, Any], snapshot_obj: Any) -> dict[str, Any]:
    return nhis_estimate_to_dict(estimate_nhis_monthly(profile=profile, snapshot_obj=snapshot_obj))


def _fallback_estimate_dict(*, profile: dict[str, Any], target_month: str, reason: str) -> dict[str, Any]:
    cycle = month_cycle_info(target_month)
    rules = get_rules(target_month, snapshot_obj=None)
    return {
        "member_type": str(profile.get("member_type") or "unknown"),
        "mode": "failed",
        "confidence_level": "low",
        "can_estimate": False,
        "health_est_krw": 0,
        "ltc_est_krw": 0,
        "total_est_krw": 0,
        "health_premium_krw": 0,
        "ltc_premium_krw": 0,
        "total_premium_krw": 0,
        "income_points": 0.0,
        "property_points": 0.0,
        "vehicle_points": 0.0,
        "total_points": 0.0,
        "applied_floor": False,
        "applied_cap": False,
        "point_value_used": float(rules.point_value),
        "ltc_ratio_used": float(rules.ltc_ratio_of_health),
        "scale_warning": False,
        "income_year_applied": int(cycle.get("income_year_applied") or 0),
        "property_year_applied": int(cycle.get("property_year_applied") or 0),
        "cycle_start_year": int(cycle.get("cycle_start_year") or 0),
        "warnings": ["계산에 실패해 기본값으로 표시했어요."],
        "notes": ["잠시 후 다시 시도해 주세요."],
        "basis": {"fallback_reason": reason},
    }


def estimate_month(target_month: str, profile: dict[str, Any], snapshot_obj: Any | None = None) -> dict[str, Any]:
    payload = dict(profile or {})
    month_key = parse_month_key(target_month)
    payload["target_month"] = month_key
    return estimate_nhis_monthly_dict(payload, snapshot_obj)


def _derive_zero_diff_reason(
    *,
    diff: int,
    same_cycle_active: bool,
    nov_calc_reused_current: bool,
    current: dict[str, Any],
    november: dict[str, Any],
) -> str:
    if diff != 0:
        return ""
    if nov_calc_reused_current:
        return "11월 계산에 실패해 현재값을 재사용했어요."
    if same_cycle_active:
        return "선택한 달이 이미 11월 반영 기준이 적용된 기간이라 차이가 작거나 없을 수 있어요."
    current_income_year = int(current.get("income_year_applied") or 0)
    current_property_year = int(current.get("property_year_applied") or 0)
    nov_income_year = int(november.get("income_year_applied") or 0)
    nov_property_year = int(november.get("property_year_applied") or 0)
    current_floor = bool(current.get("applied_floor"))
    nov_floor = bool(november.get("applied_floor"))
    current_cap = bool(current.get("applied_cap"))
    nov_cap = bool(november.get("applied_cap"))
    current_points = float(current.get("total_points") or 0.0)
    nov_points = float(november.get("total_points") or 0.0)
    points_equal = abs(current_points - nov_points) < 0.01
    if current_income_year == nov_income_year and current_property_year == nov_property_year:
        if current_floor and nov_floor:
            return "현재 월과 11월 모두 하한 보험료가 적용돼 차이가 없어요."
        if current_cap and nov_cap:
            return "현재 월과 11월 모두 상한 보험료가 적용돼 차이가 없어요."
        return "현재 월과 11월의 적용연도가 같아 차이가 없어요."
    if current_floor and nov_floor:
        return "적용연도는 다르지만 두 달 모두 하한 보험료가 적용돼 차이가 없어요."
    if current_cap and nov_cap:
        return "적용연도는 다르지만 두 달 모두 상한 보험료가 적용돼 차이가 없어요."
    if points_equal:
        return "적용연도는 다르지만 현재 입력 기준에서 산정 점수가 같아 차이가 없어요."
    return "현재 입력 기준에서는 11월 반영 전후 차이가 크지 않아요."


def _detect_scale_warning(est: dict[str, Any], *, has_inputs: bool) -> bool:
    if bool(est.get("scale_warning")):
        return True
    if _is_point_value_scale_suspicious(float(est.get("point_value_used") or 0.0)):
        return True
    total_krw = int(est.get("total_est_krw") or 0)
    if has_inputs and total_krw > 0 and total_krw < MIN_REASONABLE_MONTHLY_WITH_INPUT:
        return True
    total_points = float(est.get("total_points") or 0.0)
    if has_inputs and total_points < MIN_POINT_EXPECTED_WITH_INPUT:
        return True
    return False


def estimate_compare(current_month: str, profile: dict[str, Any], snapshot_obj: Any | None = None) -> dict[str, Any]:
    current_profile = dict(profile or {})
    current_month = parse_month_key(current_month)
    current_profile["target_month"] = current_month

    fallback_reasons: list[str] = []
    nov_calc_reused_current = False

    try:
        current = estimate_month(current_month, current_profile, snapshot_obj)
    except Exception:
        fallback_reasons.append("current_calc_failed")
        current = _fallback_estimate_dict(profile=current_profile, target_month=current_month, reason="current_calc_failed")

    current_total = _safe_int(current.get("total_est_krw"))

    y = int(current_month[:4])
    nov_month = f"{y:04d}-11"
    nov_profile = dict(current_profile)
    nov_profile["target_month"] = nov_month
    try:
        november = estimate_month(nov_month, nov_profile, snapshot_obj)
    except Exception:
        fallback_reasons.append("november_calc_failed")
        nov_calc_reused_current = True
        november = dict(current or {})
        november["notes"] = list(dict.fromkeys(list(november.get("notes") or []) + ["11월 반영 계산에 실패해 현재값을 임시로 보여줘요."]))
        november["warnings"] = list(dict.fromkeys(list(november.get("warnings") or []) + ["11월 반영 계산 실패로 현재값 재사용 중"]))
        november["mode"] = "nov_fallback_current"
        november["can_estimate"] = False
        november["basis"] = dict(november.get("basis") or {})
        november["basis"]["fallback_reason"] = "november_calc_failed"

    november_total = _safe_int(november.get("total_est_krw"))

    diff = int(november_total - current_total)
    current_cycle_start = int(current.get("cycle_start_year") or 0)
    november_cycle_start = int(november.get("cycle_start_year") or 0)
    same_cycle_active = bool(current_cycle_start > 0 and november_cycle_start > 0 and current_cycle_start == november_cycle_start)
    has_inputs = bool(_has_income_input(current_profile) or _has_property_input(current_profile))
    zero_diff_reason = _derive_zero_diff_reason(
        diff=diff,
        same_cycle_active=same_cycle_active,
        nov_calc_reused_current=nov_calc_reused_current,
        current=current,
        november=november,
    )
    scale_warning_current = _detect_scale_warning(current, has_inputs=has_inputs)
    scale_warning_november = _detect_scale_warning(november, has_inputs=has_inputs)
    fallback_reason_codes = list(dict.fromkeys([str(code).strip() for code in fallback_reasons if str(code).strip()]))
    fallback_reason = "+".join(fallback_reason_codes)
    fallback_used = bool(fallback_reason_codes)

    return {
        "current": current,
        "november": november,
        "current_total_krw": int(current_total),
        "november_total_krw": int(november_total),
        "diff_krw": int(diff),
        "increase_krw": int(max(0, diff)),
        "same_cycle_active": same_cycle_active,
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "fallback_reasons": fallback_reason_codes,
        "nov_calc_reused_current": bool(nov_calc_reused_current),
        "zero_diff_reason": zero_diff_reason,
        "scale_warning_current": bool(scale_warning_current),
        "scale_warning_november": bool(scale_warning_november),
        "current_cycle": {
            "income_year_applied": int(current.get("income_year_applied") or 0),
            "property_year_applied": int(current.get("property_year_applied") or 0),
            "cycle_start_year": current_cycle_start,
        },
        "november_cycle": {
            "income_year_applied": int(november.get("income_year_applied") or 0),
            "property_year_applied": int(november.get("property_year_applied") or 0),
            "cycle_start_year": november_cycle_start,
        },
    }


def estimate_nhis_current_vs_november(profile: dict[str, Any], snapshot_obj: Any) -> dict[str, Any]:
    current_profile = dict(profile or {})
    current_month = parse_month_key(current_profile.get("target_month"))
    return estimate_compare(current_month=current_month, profile=current_profile, snapshot_obj=snapshot_obj)


def build_nhis_reason_breakdown(profile: dict[str, Any], snapshot_obj: Any, total_est_krw: int) -> dict[str, Any]:
    estimate = estimate_nhis_monthly_dict(profile or {}, snapshot_obj)
    total = max(0, int(total_est_krw or estimate.get("total_est_krw") or 0))
    health = max(0, int(estimate.get("health_est_krw") or 0))

    income_h = max(0, int(estimate.get("income_premium_krw") or 0))
    property_h = max(0, int(estimate.get("property_premium_krw") or 0))
    vehicle_h = max(0, int(estimate.get("vehicle_premium_krw") or 0))

    health_sum = max(1, income_h + property_h + vehicle_h)
    if health > 0 and total > 0:
        income_amt = _round_krw(total * (income_h / health_sum))
        property_amt = _round_krw(total * (property_h / health_sum))
        vehicle_amt = max(0, total - income_amt - property_amt)
    else:
        income_amt = property_amt = vehicle_amt = 0

    def _pct(v: int) -> int:
        if total <= 0:
            return 0
        return max(0, min(100, int(round((v / total) * 100))))

    return {
        "income": {"amount_krw": int(income_amt), "percent": _pct(income_amt)},
        "property": {"amount_krw": int(property_amt), "percent": _pct(property_amt)},
        "vehicle": {"amount_krw": int(vehicle_amt), "percent": _pct(vehicle_amt)},
        "confidence": str(estimate.get("confidence_level") or "low"),
    }


def build_nhis_action_items(
    profile: dict[str, Any],
    snapshot_obj: Any,
    current_total_krw: int,
    november_total_krw: int,
) -> list[dict[str, Any]]:
    rules = get_rules_for_month(parse_month_key((profile or {}).get("target_month")), snapshot_obj=snapshot_obj)

    annual_non_salary = max(0, _safe_int((profile or {}).get("non_salary_annual_income_krw")))
    property_base = max(0, _safe_int((profile or {}).get("property_tax_base_total_krw")))
    household_has_others = bool((profile or {}).get("household_has_others") is True)

    diff = int(november_total_krw or 0) - int(current_total_krw or 0)
    increase = max(0, diff)

    actions: list[dict[str, Any]] = []

    if household_has_others:
        possible = _round_krw(max(0, current_total_krw) * 0.05)
        actions.append(
            {
                "title": "세대 합산 여부 확인",
                "desc": "실제 독립 거주·생계 요건을 충족하는 경우에만 달라질 수 있어요(추정).",
                "effect_krw": int(possible),
                "condition": "조건 충족 시",
            }
        )

    if annual_non_salary > 20_000_000:
        possible = _round_krw(((annual_non_salary - 20_000_000) / 12.0) * rules.insurance_rate * 0.2)
        actions.append(
            {
                "title": "보수 외 소득 반영 여부 점검",
                "desc": "소득 급감/휴업 등 실제 변동이 있으면 공단 조정 가능성을 확인해 보세요(추정).",
                "effect_krw": int(max(0, possible)),
                "condition": "변동 사실이 있을 때",
            }
        )

    if property_base > rules.property_basic_deduction_krw:
        possible = _round_krw((property_base - rules.property_basic_deduction_krw) * 0.00008)
        actions.append(
            {
                "title": "재산 공제/부채공제 점검",
                "desc": "요건 충족 시 재산 반영 금액이 줄어들 수 있어요(추정).",
                "effect_krw": int(max(0, possible)),
                "condition": "요건 충족 시",
            }
        )

    actions.append(
        {
            "title": "11월 대비 건보료 예비비 만들기",
            "desc": "11월 예상 증가분(추정)을 기준으로 3개월치 보관을 권장해요.",
            "effect_krw": int(max(0, increase * 3)),
            "condition": "지금부터 준비",
        }
    )

    if rules.car_points_enabled:
        actions.append(
            {
                "title": "차량 영향 점검",
                "desc": "차량 부과 기준이 적용되는 시점에만 영향이 있어요(추정).",
                "effect_krw": _round_krw(max(0, current_total_krw) * 0.03),
                "condition": "부과 기준 해당 시",
            }
        )

    return actions[:4]
