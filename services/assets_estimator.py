from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.extensions import db
from core.time import utcnow
from services.assets_data import ensure_asset_datasets
from services.assets_profile import build_assets_context
from services.income_hybrid import aggregate_income_override, pick_income_override_for_month
from services.nhis_estimator import estimate_nhis_current_vs_november
from services.official_refs.guard import check_nhis_ready
from services.nhis_profile import get_or_create_nhis_profile, list_nhis_bill_history, nhis_profile_to_dict
from services.reference.nhis_reference import evaluate_rent_asset_value_krw, get_nhis_reference_snapshot
from services.nhis_rules import get_rules_for_month
from services.nhis_rates import ensure_active_snapshot, snapshot_to_display_dict
from services.risk import compute_tax_estimate


@dataclass(frozen=True)
class AssetFeedback:
    current_nhis_est_krw: int
    november_nhis_est_krw: int
    november_diff_krw: int
    tax_due_est_krw: int
    completion_ratio: int
    confidence: str
    savings_effect_krw: int
    note: str
    warnings: tuple[str, ...]


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(str(raw or "0").replace(",", "").strip()))
    except Exception:
        return int(default)


def _safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(str(raw or "0").replace(",", "").strip())
    except Exception:
        return float(default)


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _norm_key(raw: Any) -> str:
    return _normalize_text(raw).lower().replace(" ", "")


def _normalize_nhis_estimate_row(
    row: dict[str, Any] | None,
    *,
    member_type: str = "unknown",
    mode: str = "failed",
    can_estimate: bool = False,
) -> dict[str, Any]:
    payload = dict(row or {})
    payload_basis = dict(payload.get("basis") or {})
    notes = [str(item) for item in list(payload.get("notes") or []) if str(item).strip()]
    warnings = [str(item) for item in list(payload.get("warnings") or []) if str(item).strip()]

    normalized: dict[str, Any] = {
        "member_type": str(payload.get("member_type") or member_type or "unknown"),
        "mode": str(payload.get("mode") or mode),
        "confidence_level": str(payload.get("confidence_level") or "low"),
        "income_premium_krw": max(0, _safe_int(payload.get("income_premium_krw"), 0)),
        "property_premium_krw": max(0, _safe_int(payload.get("property_premium_krw"), 0)),
        "health_est_krw": max(0, _safe_int(payload.get("health_est_krw"), _safe_int(payload.get("health_premium_krw"), 0))),
        "ltc_est_krw": max(0, _safe_int(payload.get("ltc_est_krw"), _safe_int(payload.get("ltc_premium_krw"), 0))),
        "total_est_krw": max(0, _safe_int(payload.get("total_est_krw"), 0)),
        "income_points": max(0.0, _safe_float(payload.get("income_points"), 0.0)),
        "property_points": max(0.0, _safe_float(payload.get("property_points"), 0.0)),
        "income_year_applied": _safe_int(payload.get("income_year_applied"), 0),
        "property_year_applied": _safe_int(payload.get("property_year_applied"), 0),
        "can_estimate": bool(payload.get("can_estimate")) if ("can_estimate" in payload) else bool(can_estimate),
        "notes": notes,
        "warnings": warnings,
        "basis": {
            "source_year": payload_basis.get("source_year"),
            "reference_last_checked_date": payload_basis.get("reference_last_checked_date"),
            **payload_basis,
        },
    }
    return normalized


def _build_vehicle_estimate(*, car_input: dict[str, Any], vehicle_dataset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, list[str]]:
    brand = _normalize_text(car_input.get("brand"))
    model = _normalize_text(car_input.get("model"))
    year = _safe_int(car_input.get("year"), 0)
    current_year = utcnow().year
    if not brand and not model and year <= 0:
        estimated = {
            "car_standard_value_krw": 0,
            "impact_note": "차량 정보가 없어 반영하지 않았어요(추정).",
        }
        basis = {
            "source_name": vehicle_dataset.get("source_name") or "행정안전부 자동차 시가표준액 공개자료",
            "source_url": vehicle_dataset.get("source_url") or "",
            "source_year": int(vehicle_dataset.get("version_year") or current_year),
            "fetched_at": vehicle_dataset.get("fetched_at"),
            "matched_key": "no_vehicle_input",
            "calc_steps": {"formula": "차량 정보 미입력으로 0원 처리"},
            "confidence": "low",
            "note": "브랜드/차종/연식을 입력하면 자동으로 추정해요.",
        }
        return estimated, basis, "low", ["차량 정보가 없어 반영하지 않았어요."]

    base_map = dict((vehicle_dataset or {}).get("brands") or {})
    key_brand = _norm_key(brand)
    matched_key = "default"
    base_price = _safe_int(base_map.get("default"), 30_000_000)

    if key_brand:
        for k, v in base_map.items():
            if k == "default":
                continue
            if _norm_key(k) in key_brand or key_brand in _norm_key(k):
                matched_key = str(k)
                base_price = _safe_int(v, base_price)
                break

    warnings: list[str] = []
    confidence = "low"
    if brand and model and 1990 <= year <= (current_year + 1):
        confidence = "high"
    elif brand and (1990 <= year <= (current_year + 1)):
        confidence = "medium"
    elif brand:
        confidence = "low"

    if year <= 0:
        warnings.append("차량 연식이 없어서 보수적으로 추정했어요.")
        age = 8
    else:
        age = max(0, current_year - year)

    depreciation = max(0.15, 1.0 - (age * 0.08))
    model_bonus = 1.0
    model_key = _norm_key(model)
    if any(tok in model_key for tok in ["suv", "ev", "전기", "hybrid", "하이브리드"]):
        model_bonus = 1.08
    elif any(tok in model_key for tok in ["경차", "mini", "모닝", "레이"]):
        model_bonus = 0.9

    standard_value = int(max(0, round(base_price * depreciation * model_bonus)))

    estimated = {
        "car_standard_value_krw": standard_value,
        "impact_note": "현재 기준으로 차량은 건보료에 영향이 거의 없어요(추정).",
    }
    basis = {
        "source_name": vehicle_dataset.get("source_name") or "행정안전부 자동차 시가표준액 공개자료",
        "source_url": vehicle_dataset.get("source_url") or "",
        "source_year": int(vehicle_dataset.get("version_year") or current_year),
        "fetched_at": vehicle_dataset.get("fetched_at"),
        "matched_key": matched_key,
        "match_desc": f"브랜드={brand or '미입력'} / 차종={model or '미입력'} / 연식={year or '미입력'}",
        "calc_steps": {
            "base_price": base_price,
            "age_years": age,
            "depreciation": round(depreciation, 4),
            "model_bonus": round(model_bonus, 4),
            "formula": "기준가격 × 잔가율 × 모델보정",
        },
        "confidence": confidence,
        "note": "중고차 실거래가가 아닌 표준가액 기준 추정이에요.",
    }
    return estimated, basis, confidence, warnings


def _detect_region(address: str) -> str:
    text = _normalize_text(address)
    if "서울" in text:
        return "서울"
    if "경기" in text:
        return "경기"
    if "인천" in text:
        return "인천"
    if "부산" in text:
        return "부산"
    if "대구" in text:
        return "대구"
    return "default"


def _build_home_estimate(*, home_input: dict[str, Any], home_dataset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, list[str]]:
    address = _normalize_text(home_input.get("address_text"))
    home_type = _norm_key(home_input.get("home_type"))
    area_input = _safe_float(home_input.get("area_sqm"), 0.0)
    area = area_input
    manual_tax_base = _safe_int(home_input.get("property_tax_base_manual_krw"), 0)
    if manual_tax_base <= 0 and not address and area <= 0:
        estimated = {
            "home_public_price_krw": 0,
            "property_tax_base_est_krw": 0,
        }
        basis = {
            "source_name": home_dataset.get("source_name") or "국토교통부 공시가격 공개자료",
            "source_url": home_dataset.get("source_url") or "",
            "source_year": int(home_dataset.get("version_year") or utcnow().year),
            "fetched_at": home_dataset.get("fetched_at"),
            "matched_key": "no_home_input",
            "calc_steps": {"formula": "주택 정보 미입력으로 0원 처리"},
            "confidence": "low",
            "note": "주소/유형/면적을 입력하면 자동 추정해요.",
        }
        return estimated, basis, "low", ["주택 정보가 없어 반영하지 않았어요."]

    warnings: list[str] = []
    confidence = "low"

    if manual_tax_base > 0:
        estimated = {
            "home_public_price_krw": int(round(manual_tax_base / 0.6)),
            "property_tax_base_est_krw": manual_tax_base,
        }
        basis = {
            "source_name": "사용자 입력(재산세 과세표준)",
            "source_url": "",
            "source_year": utcnow().year,
            "fetched_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "matched_key": "manual_property_tax_base",
            "calc_steps": {
                "property_tax_base_input": manual_tax_base,
                "formula": "공시가격 ≈ 과세표준 / 0.6",
            },
            "confidence": "high",
            "note": "직접 입력값을 우선 사용했어요.",
        }
        return estimated, basis, "high", warnings

    region_map = dict((home_dataset or {}).get("region_price_per_sqm") or {})
    type_map = dict((home_dataset or {}).get("type_factor") or {})
    region = _detect_region(address)
    per_sqm = float(region_map.get(region) or region_map.get("default") or 3_900_000)

    if home_type in {"apartment", "apt", "아파트"}:
        type_key = "apartment"
    elif home_type in {"villa", "빌라", "연립", "다세대"}:
        type_key = "villa"
    elif home_type in {"house", "단독", "주택"}:
        type_key = "house"
    elif home_type in {"officetel", "오피스텔"}:
        type_key = "officetel"
    else:
        type_key = "default"

    type_factor = float(type_map.get(type_key) or type_map.get("default") or 0.82)

    if area <= 0:
        area = 59.0
        warnings.append("면적 정보가 없어 전용 59㎡ 기준으로 추정했어요.")
    if not address:
        warnings.append("주소 정보가 부족해 지역 평균값으로 추정했어요.")
    if type_key == "default":
        warnings.append("주택 유형이 없어 일반 주택 계수로 추정했어요.")

    public_price = int(max(0, round(per_sqm * area * type_factor)))
    tax_base = int(max(0, round(public_price * 0.60)))

    if address and type_key != "default":
        confidence = "medium"
    if address and type_key != "default" and area_input > 0:
        confidence = "high"

    estimated = {
        "home_public_price_krw": public_price,
        "property_tax_base_est_krw": tax_base,
    }
    basis = {
        "source_name": home_dataset.get("source_name") or "국토교통부 공시가격 공개자료",
        "source_url": home_dataset.get("source_url") or "",
        "source_year": int(home_dataset.get("version_year") or utcnow().year),
        "fetched_at": home_dataset.get("fetched_at"),
        "matched_key": f"region={region},type={type_key}",
        "calc_steps": {
            "price_per_sqm": int(round(per_sqm)),
            "area_sqm": round(area, 2),
            "type_factor": round(type_factor, 4),
            "formula": "공시가격(추정) = 지역단가 × 면적 × 유형계수",
            "tax_formula": "과세표준(추정) = 공시가격 × 0.6",
        },
        "confidence": confidence,
        "note": "실거래가가 아닌 공시 기준 데이터 기반 추정이에요.",
    }
    return estimated, basis, confidence, warnings


def _build_rent_estimate(*, rent_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, list[str]]:
    deposit = _safe_int(rent_input.get("rent_deposit_krw"), 0)
    monthly = _safe_int(rent_input.get("rent_monthly_krw"), 0)

    inferred_asset = int(max(0, deposit + (monthly * 12 * 10)))
    confidence = "high" if (deposit > 0 or monthly > 0) else "low"

    estimated = {
        "rent_deposit_krw": deposit,
        "rent_monthly_krw": monthly,
        "housing_asset_proxy_krw": inferred_asset,
    }
    basis = {
        "source_name": "사용자 입력(전월세)",
        "source_url": "",
        "source_year": utcnow().year,
        "fetched_at": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "matched_key": "rent_user_input",
        "calc_steps": {
            "deposit": deposit,
            "monthly": monthly,
            "formula": "거주자산 대용값 = 보증금 + (월세 × 12 × 10)",
        },
        "confidence": confidence,
        "note": "전월세 정보는 간편 추정에 보조로 반영돼요.",
    }
    warnings = []
    if confidence == "low":
        warnings.append("전월세 정보가 없어서 반영하지 못했어요.")
    return estimated, basis, confidence, warnings


def _derive_nhis_profile_from_assets(*, nhis_profile: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    out = dict(nhis_profile or {})
    p = assets.get("profile") or {}
    items = assets.get("items") or {}
    housing_mode = str(p.get("housing_mode") or "unknown")

    # 자산 페이지 즉시 피드백에서는 숨겨진 단건 고지서 override(last_bill_*)로
    # 결과가 고정되지 않도록 해당 경로를 비활성화한다.
    out["ignore_last_bill_override"] = True
    out["last_bill_total_krw"] = None
    out["last_bill_health_only_krw"] = None
    out["last_bill_score_points"] = None

    home_entries = list(items.get("home_list") or [])
    if not home_entries and items.get("home"):
        home_entries = [items.get("home")]
    rent_est = (((items.get("rent") or {}).get("estimated") or {}))

    if p.get("household_has_others") is not None:
        out["household_has_others"] = bool(p.get("household_has_others"))

    deposit = _safe_int(rent_est.get("rent_deposit_krw"), 0)
    monthly = _safe_int(rent_est.get("rent_monthly_krw"), 0)
    target_month = str(out.get("target_month") or utcnow().strftime("%Y-%m"))
    target_year = _safe_int(target_month[:4], utcnow().year)

    def _home_has_meaningful_input(inp: dict[str, Any]) -> bool:
        if _normalize_text(inp.get("address_text")):
            return True
        if _normalize_text(inp.get("home_type")):
            return True
        if _safe_float(inp.get("area_sqm"), 0.0) > 0:
            return True
        if _safe_int(inp.get("property_tax_base_manual_krw"), 0) > 0:
            return True
        if _safe_int(inp.get("rent_deposit_krw"), 0) > 0:
            return True
        if _safe_int(inp.get("rent_monthly_krw"), 0) > 0:
            return True
        return False

    property_tax_est = 0
    home_rent_rows: list[tuple[int, str, int, int]] = []
    has_meaningful_home_input = False
    for idx, home in enumerate(home_entries):
        node = (home or {})
        est = (node.get("estimated") or {})
        inp = (node.get("input") or {})
        if _home_has_meaningful_input(inp):
            has_meaningful_home_input = True
        property_tax_est += _safe_int(est.get("property_tax_base_est_krw"), 0)
        rental_mode = str(inp.get("rental_mode") or "").strip().lower()
        if rental_mode not in {"jeonse", "rent"}:
            continue
        d = _safe_int(inp.get("rent_deposit_krw"), 0)
        m = _safe_int(inp.get("rent_monthly_krw"), 0)
        if d <= 0 and m <= 0:
            continue
        home_rent_rows.append((idx, rental_mode, d, m))

    dedup_home_idx: int | None = None
    overlap_status = "none"
    if housing_mode in {"rent", "jeonse"} and (deposit > 0 or monthly > 0):
        matching_rows = [row for row in home_rent_rows if row[1] == housing_mode and row[2] == deposit and row[3] == monthly]
        # 중복 제거는 "단일 임대 주택 1건이 현재 거주 전월세와 완전히 일치"할 때만 적용한다.
        # 다중 임대 주택에서 금액만 같은 경우는 과탐지 우려가 있어 dedup하지 않는다.
        if len(home_rent_rows) == 1 and len(matching_rows) == 1:
            dedup_home_idx = int(matching_rows[0][0])
            overlap_status = "matched"
        elif home_rent_rows:
            overlap_status = "unknown"

    owned_home_rent_eval = 0
    for idx, _, d, m in home_rent_rows:
        if dedup_home_idx is not None and idx == dedup_home_idx:
            continue
        # 보유 주택 임대 전월세는 지역보험료 전월세 평가식((보증금 + 월세*40) * 0.30)으로 반영한다.
        owned_home_rent_eval += max(
            0,
            evaluate_rent_asset_value_krw(deposit_krw=int(d), monthly_krw=int(m), target_year=int(target_year)),
        )

    existing_property_tax = _safe_int(out.get("property_tax_base_total_krw"), 0)
    if property_tax_est > 0:
        out["property_tax_base_total_krw"] = property_tax_est
        out["asset_property_unknown"] = False
    elif has_meaningful_home_input and existing_property_tax > 0:
        # 추정값이 비었어도 기존 확정 입력을 유지해 0점 확정을 피한다.
        out["property_tax_base_total_krw"] = existing_property_tax
        out["asset_property_unknown"] = True
    else:
        out["property_tax_base_total_krw"] = 0
        out["asset_property_unknown"] = bool(has_meaningful_home_input)
    out["owned_home_rent_eval_krw"] = int(max(0, owned_home_rent_eval))
    out["asset_rent_overlap_status"] = overlap_status
    out["asset_rent_overlap_unknown"] = bool(overlap_status == "unknown")
    out["duplication_suspected"] = bool(overlap_status == "unknown")
    out["asset_rent_overlap_candidate_count"] = int(len(home_rent_rows))

    if housing_mode in {"own", "none", "unknown"}:
        out["rent_deposit_krw"] = 0
        out["rent_monthly_krw"] = 0
        out["asset_current_rent_unknown"] = False
    else:
        # 전월세 모드가 바뀌거나 값이 비워져도 이전 값이 잔존하지 않도록
        # 현재 입력만으로 값을 재결정한다.
        out["rent_deposit_krw"] = max(0, deposit)
        if housing_mode == "rent":
            out["rent_monthly_krw"] = max(0, monthly)
        else:
            out["rent_monthly_krw"] = 0

        resolved_deposit = _safe_int(out.get("rent_deposit_krw"), 0)
        resolved_monthly = _safe_int(out.get("rent_monthly_krw"), 0)
        if housing_mode == "jeonse":
            out["asset_current_rent_unknown"] = bool(resolved_deposit <= 0)
        elif housing_mode == "rent":
            out["asset_current_rent_unknown"] = bool(resolved_monthly <= 0)
        else:
            out["asset_current_rent_unknown"] = bool(resolved_deposit <= 0 and resolved_monthly <= 0)

    other_income_annual = _safe_int(p.get("other_income_annual_krw"), 0)
    out["non_salary_annual_income_krw"] = int(max(0, other_income_annual))

    if str(out.get("member_type") or "unknown") == "unknown":
        out["member_type"] = "regional"

    if str(out.get("member_type") or "unknown") == "regional":
        # 자산 페이지(지역 추정)에서는 기타소득 연간값을 소득 기준으로 고정하고,
        # 이전 화면에서 남은 연소득 fallback/월급 값으로 역전되지 않게 한다.
        out["annual_income_krw"] = int(max(0, other_income_annual))
        out["salary_monthly_krw"] = 0

    return out


def _confidence_from_parts(parts: list[str]) -> str:
    if "high" in parts:
        return "high"
    if "medium" in parts:
        return "medium"
    return "low"


def _build_nhis_whatis_payload(
    *,
    month_key: str,
    snapshot_obj: Any,
    asset_profile: dict[str, Any],
    derived_nhis: dict[str, Any],
    current_est: dict[str, Any],
    current_total: int,
) -> dict[str, Any]:
    rules = get_rules_for_month(target_month=month_key, snapshot_obj=snapshot_obj)
    basis = dict(current_est.get("basis") or {})
    calc_steps = dict(basis.get("calc_steps") or {})

    member_type = str(derived_nhis.get("member_type") or current_est.get("member_type") or "unknown").strip().lower()
    if member_type not in {"regional", "employee", "dependent", "unknown"}:
        member_type = "unknown"

    fin_interest = max(
        _safe_int(derived_nhis.get("annual_interest_krw"), 0),
        _safe_int(derived_nhis.get("income_interest_annual_krw"), 0),
    )
    fin_dividend = max(
        _safe_int(derived_nhis.get("annual_dividend_krw"), 0),
        _safe_int(derived_nhis.get("income_dividend_annual_krw"), 0),
    )
    annual_fin_income = max(0, fin_interest + fin_dividend)
    if annual_fin_income <= 0:
        selected_types = set(asset_profile.get("other_income_types") or [])
        if "interest" in selected_types or "dividend" in selected_types:
            annual_fin_income = max(0, _safe_int(asset_profile.get("other_income_annual_krw"), 0))

    rent_deposit = max(0, _safe_int(derived_nhis.get("rent_deposit_krw"), 0))
    rent_monthly = max(0, _safe_int(derived_nhis.get("rent_monthly_krw"), 0))
    property_tax_base_total = max(0, _safe_int(derived_nhis.get("property_tax_base_total_krw"), 0))
    owned_home_rent_eval = max(0, _safe_int(derived_nhis.get("owned_home_rent_eval_krw"), 0))
    salary_monthly = max(0, _safe_int(derived_nhis.get("salary_monthly_krw"), 0))

    income_premium = max(
        0,
        _safe_int(
            calc_steps.get("income_premium_step1_krw"),
            _safe_int(current_est.get("income_premium_krw"), 0),
        ),
    )
    property_premium = max(
        0,
        _safe_int(
            calc_steps.get("property_premium_step3_krw"),
            _safe_int(current_est.get("property_premium_krw"), 0),
        ),
    )
    health_est = max(
        0,
        _safe_int(
            calc_steps.get("health_premium_step4_krw"),
            _safe_int(current_est.get("health_est_krw"), _safe_int(current_est.get("health_premium_krw"), 0)),
        ),
    )
    ltc_est = max(
        0,
        _safe_int(
            calc_steps.get("ltc_premium_step5_krw"),
            _safe_int(current_est.get("ltc_est_krw"), _safe_int(current_est.get("ltc_premium_krw"), 0)),
        ),
    )
    total_est = max(
        0,
        _safe_int(
            calc_steps.get("total_premium_step6_krw"),
            _safe_int(current_est.get("total_est_krw"), _safe_int(current_total, 0)),
        ),
    )
    property_base_after_deduction = max(
        0,
        _safe_int(
            calc_steps.get("property_base_after_deduction_krw"),
            _safe_int(calc_steps.get("net_property_krw"), 0),
        ),
    )
    property_points = max(
        0.0,
        _safe_float(
            calc_steps.get("property_points_step2"),
            _safe_float(current_est.get("property_points"), 0.0),
        ),
    )

    table_rows = [[int(upper), float(points)] for upper, points in (rules.property_points_table or ())]
    housing_mode = str(asset_profile.get("housing_mode") or "unknown").strip().lower()
    debug_missing: list[str] = []
    if member_type == "regional" and float(rules.point_value or 0.0) <= 0:
        debug_missing.append("regional_point_value")
    if member_type == "regional" and not table_rows:
        debug_missing.append("property_points_table")
    if float(rules.insurance_rate or 0.0) <= 0:
        debug_missing.append("health_insurance_rate")
    if float(rules.ltc_ratio_of_health or 0.0) < 0:
        debug_missing.append("long_term_care_ratio")

    ready = not debug_missing
    return {
        "ready": bool(ready),
        "error_message": ("" if ready else "지금은 가정 계산을 할 수 없어요. 입력을 저장하면 정확도가 올라가요."),
        "debug_missing": debug_missing,
        "base": {
            "target_month": month_key,
            "member_type": member_type,
            "salary_monthly_krw": int(salary_monthly),
            "annual_fin_income_krw": int(annual_fin_income),
            "rent_deposit_krw": int(rent_deposit),
            "rent_monthly_krw": int(rent_monthly),
            "property_tax_base_total_krw": int(property_tax_base_total),
            "owned_home_rent_eval_krw": int(owned_home_rent_eval),
            "income_premium_krw": int(income_premium),
            "property_premium_krw": int(property_premium),
            "health_est_krw": int(health_est),
            "ltc_est_krw": int(ltc_est),
            "total_est_krw": int(total_est),
            "property_base_after_deduction_krw": int(property_base_after_deduction),
            "property_points": float(property_points),
        },
        "rules": {
            "health_insurance_rate": float(max(0.0, rules.insurance_rate)),
            "regional_point_value": float(max(0.0, rules.point_value)),
            "long_term_care_ratio_of_health": float(max(0.0, rules.ltc_ratio_of_health)),
            "health_premium_floor_krw": int(max(0, rules.health_premium_floor_krw)),
            "health_premium_cap_krw": int(max(0, rules.health_premium_cap_krw)),
            "property_basic_deduction_krw": int(max(0, rules.property_basic_deduction_krw)),
            "rent_eval_multiplier": float(max(0.0, rules.rent_eval_multiplier)),
            "rent_month_to_deposit_multiplier": int(max(0, rules.rent_month_to_deposit_multiplier)),
            "property_points_table": table_rows,
            "property_points_table_loaded": bool(rules.property_points_table_loaded),
            "rules_version": str(rules.rules_version),
        },
        "ui_flags": {
            "show_fin_to_10m": bool(8_000_000 <= annual_fin_income <= 12_000_000),
            "show_monthly_rent_delta": bool(housing_mode == "rent" or rent_monthly > 0),
            "housing_mode": housing_mode,
        },
        "notes": [
            "금융소득 합계가 1,000만 전후면 반영 방식이 달라질 수 있어요(추정).",
        ],
        "flags": {
            "asset_rent_overlap_unknown": _safe_bool(derived_nhis.get("asset_rent_overlap_unknown"), False),
            "asset_property_unknown": _safe_bool(derived_nhis.get("asset_property_unknown"), False),
            "asset_current_rent_unknown": _safe_bool(derived_nhis.get("asset_current_rent_unknown"), False),
        },
    }


def build_assets_feedback(user_pk: int, month_key: str) -> dict[str, Any]:
    assets_ctx = build_assets_context(user_pk, month_key=month_key)
    asset_profile = assets_ctx.get("profile") or {}
    asset_items = assets_ctx.get("items") or {}

    try:
        dataset_status = ensure_asset_datasets(refresh_if_stale_days=30)
    except Exception:
        db.session.rollback()
        dataset_status = type("DatasetStatus", (), {
            "datasets": {"vehicle": {}, "home": {}},
            "update_error": "dataset_unavailable",
            "is_stale": True,
            "used_fallback": True,
            "format_drift_keys": tuple(),
        })()
    vehicle_dataset = dataset_status.datasets.get("vehicle") or {}
    home_dataset = dataset_status.datasets.get("home") or {}

    warnings: list[str] = []
    confidence_parts: list[str] = []

    # asset estimates (다중 자산 합산)
    home_rows = list(assets_ctx.get("home_list") or [])
    if not home_rows and asset_items.get("home"):
        home_rows = [asset_items.get("home")]
    if not home_rows:
        home_rows = [{"kind": "home", "label": "보유주택 1", "input": {}}]

    home_items: list[dict[str, Any]] = []
    home_public_sum = 0
    home_tax_base_sum = 0
    for row in home_rows:
        h_input = ((row or {}).get("input") or {})
        h_est, h_basis, h_conf, h_warn = _build_home_estimate(home_input=h_input, home_dataset=home_dataset)
        confidence_parts.append(h_conf)
        warnings.extend(h_warn)
        home_public_sum += _safe_int(h_est.get("home_public_price_krw"), 0)
        home_tax_base_sum += _safe_int(h_est.get("property_tax_base_est_krw"), 0)
        home_items.append(
            {
                "kind": "home",
                "label": str((row or {}).get("label") or "보유주택"),
                "input": h_input,
                "estimated": h_est,
                "basis": h_basis,
                "warnings": h_warn,
            }
        )
    home_primary = home_items[0] if home_items else {"kind": "home", "label": "보유주택", "input": {}, "estimated": {}, "basis": {}}
    home_est = {
        "home_public_price_krw": int(max(0, home_public_sum)),
        "property_tax_base_est_krw": int(max(0, home_tax_base_sum)),
    }
    home_basis = dict(home_primary.get("basis") or {})
    home_basis["calc_steps"] = dict(home_basis.get("calc_steps") or {})
    home_basis["calc_steps"]["owned_home_count"] = len(home_items)
    home_basis["calc_steps"]["home_public_price_sum_krw"] = int(max(0, home_public_sum))
    home_basis["calc_steps"]["property_tax_base_sum_krw"] = int(max(0, home_tax_base_sum))
    home_input = dict(home_primary.get("input") or {})
    home_conf = home_basis.get("confidence") or "low"
    home_warn = list(home_primary.get("warnings") or [])

    car_rows = list(assets_ctx.get("car_list") or [])
    if not car_rows and asset_items.get("car"):
        car_rows = [asset_items.get("car")]
    if not car_rows:
        car_rows = [{"kind": "car", "label": "차량 1", "input": {}}]

    car_items: list[dict[str, Any]] = []
    car_value_sum = 0
    for row in car_rows:
        c_input = ((row or {}).get("input") or {})
        c_est, c_basis, c_conf, c_warn = _build_vehicle_estimate(car_input=c_input, vehicle_dataset=vehicle_dataset)
        confidence_parts.append(c_conf)
        warnings.extend(c_warn)
        car_value_sum += _safe_int(c_est.get("car_standard_value_krw"), 0)
        car_items.append(
            {
                "kind": "car",
                "label": str((row or {}).get("label") or "차량"),
                "input": c_input,
                "estimated": c_est,
                "basis": c_basis,
                "warnings": c_warn,
            }
        )
    car_primary = car_items[0] if car_items else {"kind": "car", "label": "차량", "input": {}, "estimated": {}, "basis": {}}
    car_est = {"car_standard_value_krw": int(max(0, car_value_sum))}
    car_basis = dict(car_primary.get("basis") or {})
    car_basis["calc_steps"] = dict(car_basis.get("calc_steps") or {})
    car_basis["calc_steps"]["car_count"] = len(car_items)
    car_basis["calc_steps"]["car_standard_value_sum_krw"] = int(max(0, car_value_sum))
    car_input = dict(car_primary.get("input") or {})
    car_conf = car_basis.get("confidence") or "low"
    car_warn = list(car_primary.get("warnings") or [])

    rent_input = ((asset_items.get("rent") or {}).get("input") or {})
    rent_est, rent_basis, rent_conf, rent_warn = _build_rent_estimate(rent_input=rent_input)
    confidence_parts.append(rent_conf)
    warnings.extend(rent_warn)

    # nhis estimates
    nhis_guard = check_nhis_ready()
    nhis_ready = bool(nhis_guard.get("ready"))
    nhis_status = None
    nhis_snapshot = snapshot_to_display_dict(None)
    nhis_profile = nhis_profile_to_dict(None)
    if nhis_ready:
        try:
            nhis_status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6)
            nhis_snapshot = snapshot_to_display_dict(nhis_status.snapshot)
        except Exception:
            db.session.rollback()
            nhis_ready = False
            warnings.append("공식 기준 스냅샷을 불러오지 못해 건보료 숫자를 숨겼어요.")
    else:
        warnings.append(
            str(nhis_guard.get("message") or "공식 기준 업데이트가 필요해요. 잠시 후 다시 시도해 주세요.")
        )
    try:
        nhis_profile = nhis_profile_to_dict(get_or_create_nhis_profile(user_pk))
        nhis_profile["bill_history"] = list_nhis_bill_history(user_pk)
    except Exception:
        db.session.rollback()
        warnings.append("건보료 프로필을 불러오지 못해 입력값 일부가 반영되지 않았어요.")
    nhis_member_type_input = str(nhis_profile.get("member_type") or "unknown").strip().lower()
    if nhis_member_type_input not in {"regional", "employee", "dependent", "unknown"}:
        nhis_member_type_input = "unknown"

    fetched_at_raw = nhis_snapshot.get("fetched_at")
    if hasattr(fetched_at_raw, "strftime"):
        nhis_snapshot["fetched_at_text"] = fetched_at_raw.strftime("%Y-%m-%d %H:%M")
    elif fetched_at_raw:
        nhis_snapshot["fetched_at_text"] = str(fetched_at_raw)
    else:
        nhis_snapshot["fetched_at_text"] = "-"

    merged_assets = {
        "profile": asset_profile,
        "items": {
            "car": {"estimated": car_est, "basis": car_basis, "input": car_input, "warnings": car_warn},
            "car_list": car_items,
            "home": {"estimated": home_est, "basis": home_basis, "input": home_input, "warnings": home_warn},
            "home_list": home_items,
            "rent": {"estimated": rent_est, "basis": rent_basis, "input": rent_input},
        },
    }
    derived_nhis = _derive_nhis_profile_from_assets(nhis_profile=nhis_profile, assets=merged_assets)
    derived_nhis["target_month"] = month_key
    nhis_income_source = {
        "source_code": "auto",
        "source_label": "자동 추정(연동)",
        "target_year": 0,
        "used_year": None,
        "used_scope": None,
    }
    nhis_income_override_values: dict[str, Any] = {}
    try:
        override_pick = pick_income_override_for_month(
            user_pk=int(user_pk),
            month_key=month_key,
            purpose="nhis",
        )
        nhis_income_source = {
            "source_code": str(override_pick.get("source_code") or "auto"),
            "source_label": str(override_pick.get("source_label") or "자동 추정(연동)"),
            "target_year": int(override_pick.get("target_year") or 0),
            "used_year": (
                int(override_pick.get("used_year"))
                if override_pick.get("used_year") is not None
                else None
            ),
            "used_scope": override_pick.get("used_scope"),
        }
        if bool(override_pick.get("applied")) and isinstance(override_pick.get("entry"), dict):
            override_entry = dict(override_pick.get("entry") or {})
            override_agg = aggregate_income_override(override_entry)
            salary_annual = int(max(0, override_agg.get("salary_income_amount_krw") or 0))
            salary_monthly = int(max(0, round(salary_annual / 12)))

            derived_nhis.update(
                {
                    "annual_business_income_krw": int(max(0, override_agg.get("business_income_amount_krw") or 0)),
                    "annual_interest_krw": int(max(0, override_agg.get("fin_income_amount_krw") or 0)),
                    "annual_dividend_krw": 0,
                    "annual_salary_krw": int(salary_annual),
                    "annual_pension_krw": int(max(0, override_agg.get("pension_income_amount_krw") or 0)),
                    "annual_other_krw": int(max(0, override_agg.get("other_income_amount_krw") or 0)),
                    "salary_monthly_krw": int(salary_monthly),
                    "non_salary_annual_income_krw": int(max(0, override_agg.get("non_salary_annual_income_krw") or 0)),
                    "annual_income_krw": int(max(0, override_agg.get("annual_total_income_krw") or 0)),
                }
            )
            nhis_income_override_values = {
                **override_agg,
                "input_basis": str(override_entry.get("input_basis") or ""),
                "is_pre_tax": bool(override_entry.get("is_pre_tax") is not False),
            }
    except Exception:
        warnings.append("사용자 소득 입력을 불러오지 못해 자동 추정으로 계산했어요.")

    if bool(derived_nhis.get("asset_rent_overlap_unknown")):
        warnings.append("현재 거주 전월세와 보유 주택 임대 정보가 일부 겹칠 수 있어 보수적으로 반영했어요.")
        confidence_parts.append("low")
    if bool(derived_nhis.get("asset_property_unknown")):
        warnings.append("보유 주택 재산 정보가 부족해 일부 항목은 이전값/보수값으로 추정했어요.")
        confidence_parts.append("low")
    if bool(derived_nhis.get("asset_current_rent_unknown")):
        warnings.append("현재 거주 전월세 정보가 부족해 일부 항목은 이전값/보수값으로 추정했어요.")
        confidence_parts.append("low")

    if nhis_ready and nhis_status and nhis_status.snapshot is not None:
        try:
            compare = estimate_nhis_current_vs_november(derived_nhis, nhis_status.snapshot)
            current_est = _normalize_nhis_estimate_row(
                dict(compare.get("current") or {}),
                member_type=str(derived_nhis.get("member_type") or "unknown"),
                mode="estimated",
                can_estimate=True,
            )
            nov_est = _normalize_nhis_estimate_row(
                dict(compare.get("november") or {}),
                member_type=str(derived_nhis.get("member_type") or "unknown"),
                mode="estimated",
                can_estimate=True,
            )
            current_total_raw = compare.get("current_total_krw")
            november_total_raw = compare.get("november_total_krw")
            current_total = (
                _safe_int(current_total_raw, 0)
                if current_total_raw is not None
                else _safe_int(current_est.get("total_est_krw"), 0)
            )
            november_total = (
                _safe_int(november_total_raw, 0)
                if november_total_raw is not None
                else _safe_int(nov_est.get("total_est_krw"), 0)
            )
        except Exception:
            db.session.rollback()
            compare = {
                "current_total_krw": 0,
                "november_total_krw": 0,
                "diff_krw": 0,
                "current": {},
                "november": {},
                "fallback_used": True,
                "fallback_reason": "nhis_calc_failed",
            }
            current_est = _normalize_nhis_estimate_row(
                {
                    "member_type": str(derived_nhis.get("member_type") or "unknown"),
                    "mode": "failed",
                    "confidence_level": "low",
                    "health_est_krw": 0,
                    "ltc_est_krw": 0,
                    "total_est_krw": 0,
                    "notes": ["건보료 계산에 실패해 숫자를 표시하지 않았어요."],
                    "warnings": [],
                    "can_estimate": False,
                },
                member_type=str(derived_nhis.get("member_type") or "unknown"),
                mode="failed",
                can_estimate=False,
            )
            warnings.append("건보료 계산을 완료하지 못해 숫자를 표시하지 않았어요.")
            nov_est = _normalize_nhis_estimate_row(
                {"member_type": str(derived_nhis.get("member_type") or "unknown")},
                mode="failed",
                can_estimate=False,
            )
            current_total = _safe_int(current_est.get("total_est_krw"), 0)
            november_total = current_total
    else:
        compare = {
            "current_total_krw": 0,
            "november_total_krw": 0,
            "diff_krw": 0,
            "current": {},
            "november": {},
            "fallback_used": True,
            "fallback_reason": (
                str(nhis_guard.get("reason") or "official_refs_not_ready")
                if (not nhis_ready)
                else "snapshot_missing"
            ),
        }
        current_est = _normalize_nhis_estimate_row(
            {
                "member_type": str(derived_nhis.get("member_type") or "unknown"),
                "mode": "blocked",
                "confidence_level": "low",
                "health_est_krw": 0,
                "ltc_est_krw": 0,
                "total_est_krw": 0,
                "notes": ["공식 기준 검증 상태가 준비되지 않아 숫자를 숨겼어요."],
                "warnings": [],
                "can_estimate": False,
            },
            member_type=str(derived_nhis.get("member_type") or "unknown"),
            mode="blocked",
            can_estimate=False,
        )
        warnings.append("공식 기준 검증 상태가 준비되지 않아 건보료 숫자를 표시하지 않았어요.")
        nov_est = _normalize_nhis_estimate_row(
            {"member_type": str(derived_nhis.get("member_type") or "unknown")},
            member_type=str(derived_nhis.get("member_type") or "unknown"),
            mode="blocked",
            can_estimate=False,
        )
        current_total = _safe_int(current_est.get("total_est_krw"), 0)
        november_total = current_total

    for estimate_row in (current_est, nov_est):
        if not isinstance(estimate_row, dict):
            continue
        basis = dict(estimate_row.get("basis") or {})
        basis["income_source_code"] = str(nhis_income_source.get("source_code") or "auto")
        basis["income_source_label"] = str(nhis_income_source.get("source_label") or "자동 추정(연동)")
        basis["income_source_year"] = nhis_income_source.get("used_year")
        basis["income_source_scope"] = nhis_income_source.get("used_scope")
        estimate_row["basis"] = basis
        estimate_row["income_source_code"] = basis["income_source_code"]
        estimate_row["income_source_label"] = basis["income_source_label"]
        estimate_row["income_source_year"] = basis["income_source_year"]
        estimate_row["income_source_scope"] = basis["income_source_scope"]

    # tax estimate (existing engine reuse)
    try:
        tax_est = compute_tax_estimate(user_pk=user_pk, month_key=month_key)
        tax_official_calculable = bool(getattr(tax_est, "official_calculable", True))
        tax_due_est_krw = _safe_int(getattr(tax_est, "tax_due_est_krw", 0), 0) if tax_official_calculable else 0
        tax_income_source = {
            "source_code": str(getattr(tax_est, "income_source_code", "auto") or "auto"),
            "source_label": (
                "계산 불가(공식 입력 부족)"
                if (not tax_official_calculable)
                else str(getattr(tax_est, "income_source_label", "자동 추정(연동)") or "자동 추정(연동)")
            ),
            "used_year": getattr(tax_est, "income_source_year", None),
            "target_year": int(getattr(tax_est, "income_source_target_year", 0) or 0),
            "applied": bool(getattr(tax_est, "income_override_applied", False)),
        }
        if not tax_official_calculable:
            warnings.append("세금 추정은 공식 입력(과세표준)이 부족해 숫자를 숨겼어요.")
    except Exception:
        db.session.rollback()
        tax_due_est_krw = 0
        tax_income_source = {
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "used_year": None,
            "target_year": 0,
            "applied": False,
        }
        warnings.append("세금 추정 계산을 완료하지 못해 기본값으로 표시했어요.")

    completion_ratio = int(assets_ctx.get("completion_ratio") or 0)
    confidence = _confidence_from_parts(confidence_parts + [str(current_est.get("confidence_level") or "low")])
    savings_effect = max(0, current_total - november_total)

    note = "좋아요! 11월 예상 보험료가 더 정확해졌어요."
    if str(current_est.get("mode") or "").startswith("bill_"):
        note = "고지서 기반 이력이 반영되어 정확도가 올라갔어요."
    if completion_ratio < 50:
        note = "입력을 조금 더 하면 11월 예상 금액이 더 정확해져요."
    if not nhis_ready:
        note = str(nhis_guard.get("message") or "공식 기준 업데이트가 필요해요. 잠시 후 다시 시도해 주세요.")

    if dataset_status.update_error:
        warnings.append("기준 데이터 업데이트에 실패해 마지막 데이터로 추정했어요.")
    if dataset_status.used_fallback:
        warnings.append("일부 기준 데이터는 기본값으로 추정 중이에요.")
    if getattr(dataset_status, "format_drift_keys", ()):
        warnings.append("공식 페이지 형식 변경으로 최신 갱신을 건너뛰고, 마지막 검증 기준으로 추정했어요.")

    if nhis_ready and nhis_status and nhis_status.snapshot is not None:
        try:
            nhis_whatis_payload = _build_nhis_whatis_payload(
                month_key=month_key,
                snapshot_obj=nhis_status.snapshot,
                asset_profile=asset_profile,
                derived_nhis=derived_nhis,
                current_est=current_est,
                current_total=current_total,
            )
        except Exception:
            ref = get_nhis_reference_snapshot(_safe_int(month_key[:4], utcnow().year))
            nhis_whatis_payload = {
                "ready": False,
                "error_message": "지금은 가정 계산을 할 수 없어요. 입력을 저장하면 정확도가 올라가요.",
                "debug_missing": ["whatis_payload_build_failed"],
                "base": {
                    "target_month": month_key,
                    "member_type": str(derived_nhis.get("member_type") or "unknown"),
                    "salary_monthly_krw": max(0, _safe_int(derived_nhis.get("salary_monthly_krw"), 0)),
                    "annual_fin_income_krw": max(0, _safe_int(derived_nhis.get("non_salary_annual_income_krw"), 0)),
                    "rent_deposit_krw": max(0, _safe_int(derived_nhis.get("rent_deposit_krw"), 0)),
                    "rent_monthly_krw": max(0, _safe_int(derived_nhis.get("rent_monthly_krw"), 0)),
                    "property_tax_base_total_krw": max(0, _safe_int(derived_nhis.get("property_tax_base_total_krw"), 0)),
                    "owned_home_rent_eval_krw": max(0, _safe_int(derived_nhis.get("owned_home_rent_eval_krw"), 0)),
                    "income_premium_krw": max(0, _safe_int(current_est.get("income_premium_krw"), 0)),
                    "property_premium_krw": max(0, _safe_int(current_est.get("property_premium_krw"), 0)),
                    "health_est_krw": max(0, _safe_int(current_est.get("health_est_krw"), 0)),
                    "ltc_est_krw": max(0, _safe_int(current_est.get("ltc_est_krw"), 0)),
                    "total_est_krw": max(0, int(current_total)),
                    "property_base_after_deduction_krw": 0,
                    "property_points": 0.0,
                },
                "rules": {
                    "health_insurance_rate": float(nhis_snapshot.get("health_insurance_rate") or 0.0),
                    "regional_point_value": float(nhis_snapshot.get("regional_point_value") or 0.0),
                    "long_term_care_ratio_of_health": float(nhis_snapshot.get("long_term_care_ratio_of_health") or 0.0),
                    "health_premium_floor_krw": 0,
                    "health_premium_cap_krw": 0,
                    "property_basic_deduction_krw": int(ref.property_basic_deduction_krw),
                    "rent_eval_multiplier": float(ref.rent_eval_multiplier),
                    "rent_month_to_deposit_multiplier": int(ref.rent_month_to_deposit_multiplier),
                    "property_points_table": [],
                    "property_points_table_loaded": False,
                    "rules_version": "fallback",
                },
                "ui_flags": {
                    "show_fin_to_10m": bool(8_000_000 <= _safe_int(derived_nhis.get("non_salary_annual_income_krw"), 0) <= 12_000_000),
                    "show_monthly_rent_delta": bool(
                        str(asset_profile.get("housing_mode") or "").strip().lower() == "rent"
                        or max(0, _safe_int(derived_nhis.get("rent_monthly_krw"), 0)) > 0
                    ),
                    "housing_mode": str(asset_profile.get("housing_mode") or "unknown"),
                },
                "notes": ["금융소득 합계가 1,000만 전후면 반영 방식이 달라질 수 있어요(추정)."],
                "flags": {
                    "asset_rent_overlap_unknown": bool(derived_nhis.get("asset_rent_overlap_unknown")),
                    "asset_property_unknown": bool(derived_nhis.get("asset_property_unknown")),
                    "asset_current_rent_unknown": bool(derived_nhis.get("asset_current_rent_unknown")),
                },
            }
    else:
        ref = get_nhis_reference_snapshot(_safe_int(month_key[:4], utcnow().year))
        nhis_whatis_payload = {
            "ready": False,
            "error_message": str(nhis_guard.get("message") or "지금은 가정 계산을 할 수 없어요. 입력을 저장하면 정확도가 올라가요."),
            "debug_missing": [str(nhis_guard.get("reason") or "official_refs_not_ready")],
            "base": {
                "target_month": month_key,
                "member_type": str(derived_nhis.get("member_type") or "unknown"),
                "salary_monthly_krw": max(0, _safe_int(derived_nhis.get("salary_monthly_krw"), 0)),
                "annual_fin_income_krw": max(0, _safe_int(derived_nhis.get("non_salary_annual_income_krw"), 0)),
                "rent_deposit_krw": max(0, _safe_int(derived_nhis.get("rent_deposit_krw"), 0)),
                "rent_monthly_krw": max(0, _safe_int(derived_nhis.get("rent_monthly_krw"), 0)),
                "property_tax_base_total_krw": max(0, _safe_int(derived_nhis.get("property_tax_base_total_krw"), 0)),
                "owned_home_rent_eval_krw": max(0, _safe_int(derived_nhis.get("owned_home_rent_eval_krw"), 0)),
                "income_premium_krw": max(0, _safe_int(current_est.get("income_premium_krw"), 0)),
                "property_premium_krw": max(0, _safe_int(current_est.get("property_premium_krw"), 0)),
                "health_est_krw": max(0, _safe_int(current_est.get("health_est_krw"), 0)),
                "ltc_est_krw": max(0, _safe_int(current_est.get("ltc_est_krw"), 0)),
                "total_est_krw": max(0, int(current_total)),
                "property_base_after_deduction_krw": 0,
                "property_points": 0.0,
            },
            "rules": {
                "health_insurance_rate": float(nhis_snapshot.get("health_insurance_rate") or 0.0),
                "regional_point_value": float(nhis_snapshot.get("regional_point_value") or 0.0),
                "long_term_care_ratio_of_health": float(nhis_snapshot.get("long_term_care_ratio_of_health") or 0.0),
                "health_premium_floor_krw": 0,
                "health_premium_cap_krw": 0,
                "property_basic_deduction_krw": int(ref.property_basic_deduction_krw),
                "rent_eval_multiplier": float(ref.rent_eval_multiplier),
                "rent_month_to_deposit_multiplier": int(ref.rent_month_to_deposit_multiplier),
                "property_points_table": [],
                "property_points_table_loaded": False,
                "rules_version": "fallback",
            },
            "ui_flags": {
                "show_fin_to_10m": bool(8_000_000 <= _safe_int(derived_nhis.get("non_salary_annual_income_krw"), 0) <= 12_000_000),
                "show_monthly_rent_delta": bool(
                    str(asset_profile.get("housing_mode") or "").strip().lower() == "rent"
                    or max(0, _safe_int(derived_nhis.get("rent_monthly_krw"), 0)) > 0
                ),
                "housing_mode": str(asset_profile.get("housing_mode") or "unknown"),
            },
            "notes": ["금융소득 합계가 1,000만 전후면 반영 방식이 달라질 수 있어요(추정)."],
            "flags": {
                "asset_rent_overlap_unknown": bool(derived_nhis.get("asset_rent_overlap_unknown")),
                "asset_property_unknown": bool(derived_nhis.get("asset_property_unknown")),
                "asset_current_rent_unknown": bool(derived_nhis.get("asset_current_rent_unknown")),
            },
        }

    return {
        "current_nhis_est_krw": int(max(0, current_total)),
        "november_nhis_est_krw": int(max(0, november_total)),
        "november_diff_krw": int(max(0, november_total - current_total)),
        "tax_due_est_krw": int(max(0, tax_due_est_krw)),
        "completion_ratio": max(0, min(100, completion_ratio)),
        "confidence": confidence,
        "savings_effect_krw": int(max(0, savings_effect)),
        "note": note,
        "warnings": warnings[:4],
        "dataset_status": {
            "update_error": dataset_status.update_error,
            "is_stale": bool(dataset_status.is_stale),
            "used_fallback": bool(dataset_status.used_fallback),
            "format_drift_keys": list(getattr(dataset_status, "format_drift_keys", ())),
        },
        "dataset": {
            "vehicle": vehicle_dataset,
            "home": home_dataset,
        },
        "items": merged_assets["items"],
        "derived_nhis_profile": derived_nhis,
        "nhis_income_source": nhis_income_source,
        "nhis_income_override_values": nhis_income_override_values,
        "tax_income_source": tax_income_source,
        "nhis_member_type_input": nhis_member_type_input,
        "nhis_snapshot": nhis_snapshot,
        "nhis_guard": nhis_guard,
        "nhis_estimate": current_est,
        "nhis_compare": compare,
        "nhis_november_estimate": nov_est,
        "nhis_whatis_payload": nhis_whatis_payload,
        "assets_profile": asset_profile,
    }
