from __future__ import annotations

from datetime import datetime

from core.extensions import db
from domain.models import Settings, TaxProfile

FREELANCER_TYPES = {
    "developer",
    "designer",
    "marketer",
    "creator",
    "consultant",
    "other",
}

INCOME_BANDS = {
    "lt_3m",
    "3m_6m",
    "6m_10m",
    "gt_10m",
}

WORK_MODES = {
    "solo",
    "with_tax_accountant",
    "with_team",
}

PRIMARY_GOALS = {
    "tax_ready",
    "evidence_clean",
    "faster_month_close",
}

FOCUS_ORDERS = {
    "tax_ready": ["receipt_required", "expense_confirm", "income_confirm", "receipt_attach"],
    "evidence_clean": ["receipt_required", "receipt_attach", "expense_confirm", "income_confirm"],
    "faster_month_close": ["expense_confirm", "receipt_required", "receipt_attach", "income_confirm"],
}

TAX_INDUSTRY_GROUPS = {
    "it",
    "design",
    "marketing",
    "creator",
    "consulting",
    "retail",
    "service",
    "other",
    "unknown",
}
TAX_TYPES = {"general", "simple", "exempt", "unknown"}
TAX_PREV_INCOME_BANDS = {
    "lt_30m",
    "30m_80m",
    "80m_150m",
    "150m_300m",
    "gt_300m",
    "unknown",
}
TAX_WITHHOLDING_33 = {"yes", "no", "unknown"}
TAX_INCOME_CLASSIFICATIONS = {"business", "salary", "mixed", "other", "unknown"}
TAX_PROFILE_REQUIRED_KEYS = (
    "industry_group",
    "tax_type",
    "prev_income_band",
    "withholding_3_3",
)
TAXABLE_INCOME_ANNUAL_KEYS = (
    "official_taxable_income_annual_krw",
    "taxable_income_annual_krw",
    "taxable_base_annual_krw",
    "annual_taxable_income_krw",
)
TAX_ANNUAL_GROSS_INCOME_KEYS = (
    "annual_gross_income_krw",
    "annual_total_income_krw",
    "gross_income_annual_krw",
)
TAX_ANNUAL_EXPENSE_KEYS = (
    "annual_deductible_expense_krw",
    "annual_expense_krw",
    "deductible_expense_annual_krw",
)
TAX_WITHHELD_TAX_ANNUAL_KEYS = (
    "withheld_tax_annual_krw",
    "withholding_tax_annual_krw",
    "withheld_tax_paid_annual_krw",
)
TAX_PREPAID_TAX_ANNUAL_KEYS = (
    "prepaid_tax_annual_krw",
    "interim_prepaid_tax_annual_krw",
    "paid_tax_annual_krw",
)
YES_NO_UNKNOWN = {"yes", "no", "unknown"}
OTHER_INCOME_TYPES = {"salary", "other", "interest_dividend", "pension"}
HEALTH_INSURANCE_TYPES = {"employed", "regional", "dependent", "unknown"}
TAX_INCOME_CLASSIFICATION_LABELS = {
    "business": "사업/프리랜서",
    "salary": "근로 중심",
    "mixed": "혼합",
    "other": "기타",
    "unknown": "모름",
}
TAX_INDUSTRY_LABELS = {
    "it": "IT/개발",
    "design": "디자인/영상",
    "marketing": "마케팅/광고",
    "creator": "창작/콘텐츠",
    "consulting": "강의/컨설팅",
    "retail": "도소매",
    "service": "서비스업",
    "other": "기타",
    "unknown": "모름",
}
TAX_TYPE_LABELS = {
    "general": "일반과세",
    "simple": "간이과세",
    "exempt": "면세",
    "unknown": "모름",
}
PREV_INCOME_LABELS = {
    "lt_30m": "3천만원 미만",
    "30m_80m": "3천만 ~ 8천만원",
    "80m_150m": "8천만 ~ 1억5천만원",
    "150m_300m": "1억5천만 ~ 3억원",
    "gt_300m": "3억원 이상",
    "unknown": "모름",
}
WITHHOLDING_LABELS = {
    "yes": "있음 (3.3%)",
    "no": "없음",
    "unknown": "모름",
}
YES_NO_UNKNOWN_LABELS = {
    "yes": "있음",
    "no": "없음",
    "unknown": "모름",
}
OTHER_INCOME_TYPE_LABELS = {
    "salary": "근로소득",
    "other": "기타소득",
    "interest_dividend": "이자/배당",
    "pension": "연금",
}
HEALTH_INSURANCE_TYPE_LABELS = {
    "employed": "직장가입자",
    "regional": "지역가입자",
    "dependent": "피부양자",
    "unknown": "모름",
}

TAX_REQUIRED_INPUTS_HIGH_CONFIDENCE = (
    "income_classification",
    "annual_gross_income_krw",
    "annual_deductible_expense_krw",
    "withheld_tax_annual_krw",
    "prepaid_tax_annual_krw",
    "tax_basic_inputs_confirmed",
)
TAX_REQUIRED_INPUTS_EXACT_READY = (
    "income_classification",
    "annual_gross_income_krw",
    "annual_deductible_expense_krw",
    "withheld_tax_annual_krw",
    "prepaid_tax_annual_krw",
    "tax_basic_inputs_confirmed",
    "official_taxable_income_annual_krw",
    "tax_advanced_input_confirmed",
)
TAX_OPTIONAL_SUPPORTING_INPUTS = (
    "industry_group",
    "tax_type",
    "prev_income_band",
    "withholding_3_3",
    "other_income",
    "other_income_types",
)


def _get_or_create_settings(user_pk: int) -> Settings:
    st = Settings.query.filter_by(user_pk=user_pk).first()
    if st:
        return st
    st = Settings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
    db.session.add(st)
    db.session.flush()
    return st


def onboarding_is_done(user_pk: int) -> bool:
    st = Settings.query.filter_by(user_pk=user_pk).first()
    if not st or not isinstance(st.custom_rates, dict):
        return False
    meta = st.custom_rates.get("_meta")
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("onboarding_done"))


def get_onboarding_meta(user_pk: int) -> dict:
    st = Settings.query.filter_by(user_pk=user_pk).first()
    if not st or not isinstance(st.custom_rates, dict):
        return {}
    meta = st.custom_rates.get("_meta")
    if not isinstance(meta, dict):
        return {}
    return meta


def get_primary_goal(user_pk: int) -> str | None:
    meta = get_onboarding_meta(user_pk)
    goal = meta.get("primary_goal")
    return goal if goal in PRIMARY_GOALS else None


def preferred_focus_order(goal: str | None) -> list[str]:
    return list(FOCUS_ORDERS.get(goal or "", FOCUS_ORDERS["tax_ready"]))


def pick_focus_from_counts(counts: dict[str, int], goal: str | None, default_focus: str = "receipt_required") -> str:
    order = preferred_focus_order(goal)
    for key in order:
        if int(counts.get(key, 0) or 0) > 0:
            return key
    return order[0] if order else default_focus


def save_onboarding(
    *,
    user_pk: int,
    freelancer_type: str,
    monthly_income_band: str,
    work_mode: str,
    primary_goal: str,
) -> tuple[bool, str]:
    if freelancer_type not in FREELANCER_TYPES:
        return False, "프리랜서 유형을 선택해 주세요."
    if monthly_income_band not in INCOME_BANDS:
        return False, "월 소득 구간을 선택해 주세요."
    if work_mode not in WORK_MODES:
        return False, "현재 관리 방식을 선택해 주세요."
    if primary_goal not in PRIMARY_GOALS:
        return False, "가장 중요한 목표를 선택해 주세요."

    st = _get_or_create_settings(user_pk)
    custom = dict(st.custom_rates) if isinstance(st.custom_rates, dict) else {}
    meta = custom.get("_meta") if isinstance(custom.get("_meta"), dict) else {}
    meta = dict(meta)

    meta["freelancer_type"] = freelancer_type
    meta["monthly_income_band"] = monthly_income_band
    meta["work_mode"] = work_mode
    meta["primary_goal"] = primary_goal
    meta["onboarding_done"] = True

    custom["_meta"] = meta
    st.custom_rates = custom
    db.session.commit()
    return True, "ok"


def _normalize_text(v: str | None, *, limit: int = 120) -> str:
    raw = " ".join((v or "").strip().split())
    return raw[:limit]


def _normalize_optional_annual_krw(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).replace(",", "").replace("원", "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except Exception:
        return None
    return max(0, int(value))


def _extract_taxable_income_annual_krw(profile_json: dict) -> int | None:
    for key in TAXABLE_INCOME_ANNUAL_KEYS:
        parsed = _normalize_optional_annual_krw(profile_json.get(key))
        if parsed and parsed > 0:
            return int(parsed)
    return None


def _extract_optional_annual_krw(profile_json: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        parsed = _normalize_optional_annual_krw(profile_json.get(key))
        if parsed is not None and parsed >= 0:
            return int(parsed)
    return None


def _normalize_income_classification(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in TAX_INCOME_CLASSIFICATIONS:
        return value
    return "unknown"


def validate_tax_profile_input(
    *,
    industry_group: str,
    industry_text: str,
    tax_type: str,
    prev_income_band: str,
    withholding_3_3: str,
) -> tuple[bool, str, dict]:
    ig = (industry_group or "").strip()
    tt = (tax_type or "").strip()
    pi = (prev_income_band or "").strip()
    wh = (withholding_3_3 or "").strip()
    it = _normalize_text(industry_text, limit=120)

    if ig not in TAX_INDUSTRY_GROUPS:
        return False, "업종을 선택해 주세요. 모르면 '모름'을 선택할 수 있어요.", {}
    if tt not in TAX_TYPES:
        return False, "과세 유형을 선택해 주세요. 모르면 '모름'을 선택할 수 있어요.", {}
    if pi not in TAX_PREV_INCOME_BANDS:
        return False, "전년도 수입 규모를 선택해 주세요. 모르면 '모름'을 선택할 수 있어요.", {}
    if wh not in TAX_WITHHOLDING_33:
        return False, "원천징수 여부를 선택해 주세요. 모르면 '모름'을 선택할 수 있어요.", {}

    payload = {
        "industry_group": ig,
        "industry_text": it,
        "tax_type": tt,
        "prev_income_band": pi,
        "withholding_3_3": wh,
    }
    return True, "ok", payload


def validate_tax_profile_step2_input(
    *,
    opening_date: str,
    opening_date_unknown: str,
    other_income: str,
    other_income_types: list[str],
    high_cost_asset: str,
    labor_outsource: str,
    health_insurance_type: str,
    health_insurance_monthly_krw: str,
) -> tuple[bool, str, dict]:
    od = (opening_date or "").strip()
    od_unknown = (opening_date_unknown or "").strip() == "1"

    oi = (other_income or "").strip()
    asset = (high_cost_asset or "").strip()
    labor = (labor_outsource or "").strip()
    hi_type = (health_insurance_type or "").strip()

    raw_types = [str(x or "").strip() for x in (other_income_types or [])]
    clean_types = [x for x in raw_types if x in OTHER_INCOME_TYPES]
    dedup_types: list[str] = []
    for t in clean_types:
        if t not in dedup_types:
            dedup_types.append(t)

    if od_unknown:
        opening_date_val = "unknown"
    else:
        if not od:
            return False, "개업일을 입력하거나 '모름'을 선택해 주세요.", {}
        try:
            datetime.strptime(od, "%Y-%m-%d")
        except Exception:
            return False, "개업일 형식이 올바르지 않습니다.", {}
        opening_date_val = od

    if oi not in YES_NO_UNKNOWN:
        return False, "다른 소득 여부를 선택해 주세요.", {}
    if asset not in YES_NO_UNKNOWN:
        return False, "고가장비 취득 여부를 선택해 주세요.", {}
    if labor not in YES_NO_UNKNOWN:
        return False, "인건비/외주 지급 여부를 선택해 주세요.", {}
    if hi_type not in HEALTH_INSURANCE_TYPES:
        return False, "건강보험 가입유형을 선택해 주세요.", {}

    monthly = (health_insurance_monthly_krw or "").replace(",", "").strip()
    monthly_val = None
    if monthly:
        try:
            parsed = int(float(monthly))
        except Exception:
            return False, "건강보험 월 납부액은 숫자로 입력해 주세요.", {}
        if parsed < 0:
            return False, "건강보험 월 납부액은 0 이상으로 입력해 주세요.", {}
        monthly_val = int(parsed)

    payload = {
        "opening_date": opening_date_val,
        "other_income": oi,
        "other_income_types": dedup_types if oi == "yes" else [],
        "high_cost_asset": asset,
        "labor_outsource": labor,
        "health_insurance_type": hi_type,
        "health_insurance_monthly_krw": monthly_val,
    }
    return True, "ok", payload


def get_tax_profile(user_pk: int) -> dict:
    row = TaxProfile.query.filter_by(user_pk=user_pk).first()
    base = {
        "_has_saved_profile": False,
        "industry_group": "unknown",
        "industry_text": "",
        "tax_type": "unknown",
        "prev_income_band": "unknown",
        "withholding_3_3": "unknown",
        "opening_date": "unknown",
        "other_income": "unknown",
        "other_income_types": [],
        "high_cost_asset": "unknown",
        "labor_outsource": "unknown",
        "health_insurance_type": "unknown",
        "health_insurance_monthly_krw": None,
        "official_taxable_income_annual_krw": None,
        "taxable_income_annual_krw": None,
        "annual_gross_income_krw": None,
        "annual_deductible_expense_krw": None,
        "withheld_tax_annual_krw": None,
        "prepaid_tax_annual_krw": None,
        "income_classification": "unknown",
        "taxable_income_input_source": "unknown",
        "tax_basic_inputs_confirmed": False,
        "tax_basic_inputs_confirmed_at": None,
        "tax_advanced_input_confirmed": False,
        "tax_advanced_input_confirmed_at": None,
        "wizard_last_step": 1,
        "profile_flow_done": False,
    }
    if not row or not isinstance(row.profile_json, dict):
        return base
    raw_profile = dict(row.profile_json or {})
    payload = dict(base)
    payload.update({k: raw_profile.get(k, payload.get(k)) for k in payload.keys()})
    payload["_has_saved_profile"] = True
    if not isinstance(payload.get("other_income_types"), list):
        payload["other_income_types"] = []
    payload["wizard_last_step"] = int(payload.get("wizard_last_step") or 1)
    payload["wizard_last_step"] = max(1, min(payload["wizard_last_step"], 3))
    payload["industry"] = payload.get("industry_group") or "unknown"
    payload["vat_type"] = payload.get("tax_type") or "unknown"
    payload["prev_year_revenue_band"] = payload.get("prev_income_band") or "unknown"
    payload["withholding_33"] = payload.get("withholding_3_3") or "unknown"
    payload["monthly_nhis_amount"] = payload.get("health_insurance_monthly_krw")
    taxable_income_annual_krw = _extract_taxable_income_annual_krw({**raw_profile, **payload})
    payload["official_taxable_income_annual_krw"] = taxable_income_annual_krw
    payload["taxable_income_annual_krw"] = taxable_income_annual_krw
    payload["annual_gross_income_krw"] = _extract_optional_annual_krw(
        {**raw_profile, **payload},
        TAX_ANNUAL_GROSS_INCOME_KEYS,
    )
    payload["annual_deductible_expense_krw"] = _extract_optional_annual_krw(
        {**raw_profile, **payload},
        TAX_ANNUAL_EXPENSE_KEYS,
    )
    payload["withheld_tax_annual_krw"] = _extract_optional_annual_krw(
        {**raw_profile, **payload},
        TAX_WITHHELD_TAX_ANNUAL_KEYS,
    )
    payload["prepaid_tax_annual_krw"] = _extract_optional_annual_krw(
        {**raw_profile, **payload},
        TAX_PREPAID_TAX_ANNUAL_KEYS,
    )
    payload["income_classification"] = _normalize_income_classification(payload.get("income_classification"))
    return payload


def is_tax_profile_complete(profile: dict | None) -> bool:
    p = profile if isinstance(profile, dict) else {}
    if ("_has_saved_profile" in p) and (not bool(p.get("_has_saved_profile"))):
        return False

    industry = p.get("industry_group")
    if industry in (None, ""):
        industry = p.get("industry")

    tax_type = p.get("tax_type")
    if tax_type in (None, ""):
        tax_type = p.get("vat_type")

    prev_income = p.get("prev_income_band")
    if prev_income in (None, ""):
        prev_income = p.get("prev_year_revenue_band")

    withholding = p.get("withholding_3_3")
    if withholding in (None, ""):
        withholding = p.get("withholding_33")
    ko_to_en = {"있음": "yes", "없음": "no", "모름": "unknown"}
    withholding = ko_to_en.get(str(withholding), withholding)

    return bool(
        (industry in TAX_INDUSTRY_GROUPS)
        and (tax_type in TAX_TYPES)
        and (prev_income in TAX_PREV_INCOME_BANDS)
        and (withholding in TAX_WITHHOLDING_33)
    )


def evaluate_tax_required_inputs(profile: dict | None) -> dict:
    p = dict(profile or {})
    taxable_income_annual_krw = _extract_taxable_income_annual_krw(p)
    annual_gross_income_krw = _extract_optional_annual_krw(p, TAX_ANNUAL_GROSS_INCOME_KEYS)
    annual_deductible_expense_krw = _extract_optional_annual_krw(p, TAX_ANNUAL_EXPENSE_KEYS)
    income_classification = _normalize_income_classification(p.get("income_classification"))
    withheld_tax_annual_krw = _extract_optional_annual_krw(p, TAX_WITHHELD_TAX_ANNUAL_KEYS)
    prepaid_tax_annual_krw = _extract_optional_annual_krw(p, TAX_PREPAID_TAX_ANNUAL_KEYS)

    has_taxable_income = bool(int(taxable_income_annual_krw or 0) > 0)
    has_income_classification = bool(income_classification in {"business", "salary", "mixed", "other"})
    has_annual_gross_income = bool(annual_gross_income_krw is not None)
    has_annual_deductible_expense = bool(annual_deductible_expense_krw is not None)
    has_withheld_tax_input = bool(withheld_tax_annual_krw is not None)
    has_prepaid_tax_input = bool(prepaid_tax_annual_krw is not None)
    has_basic_values = bool(
        has_income_classification
        and has_annual_gross_income
        and has_annual_deductible_expense
        and has_withheld_tax_input
        and has_prepaid_tax_input
    )
    basic_inputs_confirmed = bool(p.get("tax_basic_inputs_confirmed"))
    advanced_input_confirmed = bool(p.get("tax_advanced_input_confirmed"))

    high_confidence_missing_fields: list[str] = []
    if not has_income_classification:
        high_confidence_missing_fields.append("income_classification")
    if not has_annual_gross_income:
        high_confidence_missing_fields.append("annual_gross_income_krw")
    if not has_annual_deductible_expense:
        high_confidence_missing_fields.append("annual_deductible_expense_krw")
    if not has_withheld_tax_input:
        high_confidence_missing_fields.append("withheld_tax_annual_krw")
    if not has_prepaid_tax_input:
        high_confidence_missing_fields.append("prepaid_tax_annual_krw")
    if has_basic_values and (not basic_inputs_confirmed):
        high_confidence_missing_fields.append("tax_basic_inputs_confirmed")

    exact_ready_missing_fields = list(high_confidence_missing_fields)
    if not has_taxable_income:
        exact_ready_missing_fields.append("official_taxable_income_annual_krw")
    if has_taxable_income and (not advanced_input_confirmed):
        exact_ready_missing_fields.append("tax_advanced_input_confirmed")

    return {
        "profile_core_complete": bool(is_tax_profile_complete(p)),
        "taxable_income_annual_krw": int(taxable_income_annual_krw or 0),
        "annual_gross_income_krw": (int(annual_gross_income_krw) if annual_gross_income_krw is not None else None),
        "annual_deductible_expense_krw": (
            int(annual_deductible_expense_krw) if annual_deductible_expense_krw is not None else None
        ),
        "income_classification": income_classification,
        "withholding_3_3": str(p.get("withholding_3_3") or "unknown"),
        "withheld_tax_annual_krw": (
            int(withheld_tax_annual_krw) if withheld_tax_annual_krw is not None else None
        ),
        "prepaid_tax_annual_krw": (
            int(prepaid_tax_annual_krw) if prepaid_tax_annual_krw is not None else None
        ),
        "has_taxable_income": has_taxable_income,
        "has_annual_gross_income": has_annual_gross_income,
        "has_annual_deductible_expense": has_annual_deductible_expense,
        "has_income_classification": has_income_classification,
        "has_withholding_declared": True,
        "has_withheld_tax_input": has_withheld_tax_input,
        "has_prepaid_tax_input": has_prepaid_tax_input,
        "has_basic_values": has_basic_values,
        "basic_inputs_confirmed": basic_inputs_confirmed,
        "advanced_input_confirmed": advanced_input_confirmed,
        "high_confidence_inputs_ready": bool(not high_confidence_missing_fields),
        "exact_ready_inputs_ready": bool(not exact_ready_missing_fields),
        "high_confidence_missing_fields": high_confidence_missing_fields,
        "exact_ready_missing_fields": exact_ready_missing_fields,
        "required_fields_high_confidence": list(TAX_REQUIRED_INPUTS_HIGH_CONFIDENCE),
        "required_fields_exact_ready": list(TAX_REQUIRED_INPUTS_EXACT_READY),
        "optional_supporting_fields": list(TAX_OPTIONAL_SUPPORTING_INPUTS),
    }


def save_tax_profile(*, user_pk: int, payload: dict) -> tuple[bool, str]:
    row = TaxProfile.query.filter_by(user_pk=user_pk).first()
    if not row:
        row = TaxProfile(user_pk=user_pk, profile_json={})

    current = row.profile_json if isinstance(row.profile_json, dict) else {}
    merged = dict(current)
    merged.update(payload or {})

    taxable_touched = any((key in (payload or {})) for key in TAXABLE_INCOME_ANNUAL_KEYS)
    if taxable_touched:
        taxable_income_annual_krw = _extract_taxable_income_annual_krw(payload or {})
        merged["official_taxable_income_annual_krw"] = taxable_income_annual_krw
        merged["taxable_income_annual_krw"] = taxable_income_annual_krw
        if taxable_income_annual_krw is None:
            merged.pop("taxable_base_annual_krw", None)
            merged.pop("annual_taxable_income_krw", None)
    else:
        taxable_income_annual_krw = _extract_taxable_income_annual_krw(merged)
        merged["official_taxable_income_annual_krw"] = taxable_income_annual_krw
        merged["taxable_income_annual_krw"] = taxable_income_annual_krw

    merged["annual_gross_income_krw"] = _extract_optional_annual_krw(merged, TAX_ANNUAL_GROSS_INCOME_KEYS)
    merged["annual_deductible_expense_krw"] = _extract_optional_annual_krw(merged, TAX_ANNUAL_EXPENSE_KEYS)
    merged["withheld_tax_annual_krw"] = _extract_optional_annual_krw(merged, TAX_WITHHELD_TAX_ANNUAL_KEYS)
    merged["prepaid_tax_annual_krw"] = _extract_optional_annual_krw(merged, TAX_PREPAID_TAX_ANNUAL_KEYS)
    merged["income_classification"] = _normalize_income_classification(merged.get("income_classification"))
    merged["tax_basic_inputs_confirmed"] = bool(merged.get("tax_basic_inputs_confirmed"))
    merged["tax_advanced_input_confirmed"] = bool(merged.get("tax_advanced_input_confirmed"))
    if not merged.get("tax_basic_inputs_confirmed"):
        merged["tax_basic_inputs_confirmed_at"] = None
    if not merged.get("tax_advanced_input_confirmed"):
        merged["tax_advanced_input_confirmed_at"] = None

    row.profile_json = merged

    # 프로필에서 건보료 월 납부액이 바뀌면 Settings에도 동기화해서
    # 캘린더/기타 화면이 같은 값을 참조하도록 맞춘다.
    if isinstance(payload, dict) and ("health_insurance_monthly_krw" in payload):
        st = _get_or_create_settings(user_pk)
        raw_monthly = payload.get("health_insurance_monthly_krw")
        monthly = 0
        if raw_monthly is not None:
            try:
                monthly = int(raw_monthly)
            except Exception:
                monthly = 0
        st.nhi_monthly_krw = max(0, int(monthly or 0))
        db.session.add(st)

    db.session.add(row)
    db.session.commit()
    return True, "ok"


def tax_profile_is_complete(user_pk: int) -> bool:
    profile = get_tax_profile(user_pk)
    return bool(is_tax_profile_complete(profile))


def tax_profile_completion_meta(user_pk: int) -> dict:
    row = TaxProfile.query.filter_by(user_pk=user_pk).first()
    p = row.profile_json if row and isinstance(row.profile_json, dict) else {}

    checks = {
        "industry_group": p.get("industry_group") in TAX_INDUSTRY_GROUPS,
        "tax_type": p.get("tax_type") in TAX_TYPES,
        "prev_income_band": p.get("prev_income_band") in TAX_PREV_INCOME_BANDS,
        "withholding_3_3": p.get("withholding_3_3") in TAX_WITHHOLDING_33,
    }
    done_count = int(sum(1 for ok in checks.values() if ok))
    total = len(TAX_PROFILE_REQUIRED_KEYS)
    is_complete = bool(done_count == total)
    percent = int(round((done_count / total) * 100)) if total else 0

    return {
        "done_count": done_count,
        "total_count": total,
        "percent": percent,
        "is_complete": is_complete,
        "checks": checks,
    }


def tax_profile_summary(user_pk: int) -> dict:
    profile = get_tax_profile(user_pk)
    completion = tax_profile_completion_meta(user_pk)
    other_income_types = profile.get("other_income_types") or []
    if not isinstance(other_income_types, list):
        other_income_types = []

    industry_group = (profile.get("industry_group") or "unknown").strip()
    industry_text = _normalize_text(profile.get("industry_text") or "", limit=120)
    industry_label = TAX_INDUSTRY_LABELS.get(industry_group, "모름")
    if industry_group == "other" and industry_text:
        industry_label = f"{industry_label} ({industry_text})"

    opening_date = profile.get("opening_date") or "unknown"
    if opening_date != "unknown":
        try:
            datetime.strptime(str(opening_date), "%Y-%m-%d")
        except Exception:
            opening_date = "unknown"

    monthly_krw = profile.get("health_insurance_monthly_krw")
    try:
        monthly_krw = int(monthly_krw) if monthly_krw is not None else None
    except Exception:
        monthly_krw = None

    return {
        "is_complete": bool(completion.get("is_complete")),
        "completion_percent": int(completion.get("percent") or 0),
        "industry_group": industry_group,
        "industry_label": industry_label,
        "industry_text": industry_text,
        "tax_type": profile.get("tax_type") or "unknown",
        "tax_type_label": TAX_TYPE_LABELS.get(profile.get("tax_type"), "모름"),
        "prev_income_band": profile.get("prev_income_band") or "unknown",
        "prev_income_band_label": PREV_INCOME_LABELS.get(profile.get("prev_income_band"), "모름"),
        "withholding_3_3": profile.get("withholding_3_3") or "unknown",
        "withholding_3_3_label": WITHHOLDING_LABELS.get(profile.get("withholding_3_3"), "모름"),
        "opening_date": opening_date,
        "opening_date_label": "모름" if opening_date == "unknown" else str(opening_date),
        "other_income": profile.get("other_income") or "unknown",
        "other_income_label": YES_NO_UNKNOWN_LABELS.get(profile.get("other_income"), "모름"),
        "other_income_types": other_income_types,
        "other_income_types_labels": [OTHER_INCOME_TYPE_LABELS.get(x, x) for x in other_income_types],
        "high_cost_asset": profile.get("high_cost_asset") or "unknown",
        "high_cost_asset_label": YES_NO_UNKNOWN_LABELS.get(profile.get("high_cost_asset"), "모름"),
        "labor_outsource": profile.get("labor_outsource") or "unknown",
        "labor_outsource_label": YES_NO_UNKNOWN_LABELS.get(profile.get("labor_outsource"), "모름"),
        "health_insurance_type": profile.get("health_insurance_type") or "unknown",
        "health_insurance_type_label": HEALTH_INSURANCE_TYPE_LABELS.get(
            profile.get("health_insurance_type"), "모름"
        ),
        "health_insurance_monthly_krw": monthly_krw,
        "official_taxable_income_annual_krw": profile.get("official_taxable_income_annual_krw"),
        "annual_gross_income_krw": profile.get("annual_gross_income_krw"),
        "annual_deductible_expense_krw": profile.get("annual_deductible_expense_krw"),
        "withheld_tax_annual_krw": profile.get("withheld_tax_annual_krw"),
        "prepaid_tax_annual_krw": profile.get("prepaid_tax_annual_krw"),
        "income_classification": profile.get("income_classification") or "unknown",
        "income_classification_label": TAX_INCOME_CLASSIFICATION_LABELS.get(
            profile.get("income_classification"), "모름"
        ),
    }
