from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from core.admin_guard import admin_required, is_admin_user
from core.auth import login_required
from core.extensions import db
from core.time import utcnow
from domain.models import ActionLog, AssetProfile, BankAccountLink, NhisRateSnapshot, Settings, User, UserBankAccount
from services.assets_data import ensure_asset_datasets
from services.assets_estimator import build_assets_feedback
from services.assets_profile import (
    ASSET_QUIZ_TOTAL_STEPS,
    INCOME_TYPE_OPTIONS,
    asset_profile_to_dict,
    build_assets_context,
    get_or_create_asset_profile,
    mark_assets_completed_if_ready,
    save_assets_page,
    save_assets_quiz_step,
)
from services.analytics_events import record_input_funnel_event, record_seasonal_card_event
from services.auth import change_user_password, delete_user_account
from services.bank_accounts import (
    list_accounts_for_ui,
    merge_user_bank_accounts,
    move_account_order,
    set_account_hidden,
    undo_last_account_merge,
)
from services.input_sanitize import clamp_int, parse_bool_yn, parse_date_ym, parse_int_krw, safe_str
from services.plan import get_user_entitlements, plan_label_ko
from services.security_audit import audit_event
from services.user_messages import to_user_message, with_retry_hint
from services.official_refs.guard import check_nhis_ready, get_official_guard_status
from services.nhis_estimator import (
    build_nhis_action_items,
    build_nhis_reason_breakdown,
    estimate_nhis_current_vs_november,
)
from services.nhis_profile import nhis_profile_to_dict, save_nhis_profile_from_form
from services.nhis_profile import list_nhis_bill_history
from services.nhis_rates import ensure_active_snapshot, snapshot_to_display_dict
from services.nhis_runtime import build_nhis_recovery_cta, build_nhis_result_meta, evaluate_nhis_required_inputs
from services.nhis_unified import load_canonical_nhis_profile
from services.onboarding import (
    evaluate_tax_required_inputs,
    get_tax_profile,
    save_tax_profile,
    tax_profile_completion_meta,
    tax_profile_is_complete,
    validate_tax_profile_step2_input,
    get_onboarding_meta,
    save_onboarding,
    validate_tax_profile_input,
)
from services.seasonal_ux import (
    activate_pending_seasonal_card,
    build_seasonal_experience,
    clear_active_seasonal_card,
    clear_pending_seasonal_card,
    get_active_seasonal_card,
    seasonal_card_completion_state,
    seasonal_metric_payload_from_landing_args,
    set_active_seasonal_card,
)
from services.tax_input_draft import build_tax_input_draft

web_profile_bp = Blueprint("web_profile", __name__)

FREELANCER_TYPES = [
    ("developer", "개발/IT"),
    ("designer", "디자인/영상"),
    ("marketer", "마케팅/광고"),
    ("creator", "크리에이터/작가"),
    ("consultant", "강의/컨설팅"),
    ("other", "기타"),
]
INCOME_BANDS = [
    ("lt_3m", "300만원 미만"),
    ("3m_6m", "300만 ~ 600만원"),
    ("6m_10m", "600만 ~ 1,000만원"),
    ("gt_10m", "1,000만원 이상"),
]
WORK_MODES = [
    ("solo", "혼자 관리"),
    ("with_tax_accountant", "세무사와 함께 관리"),
    ("with_team", "팀/도구와 함께 관리"),
]
PRIMARY_GOALS = [
    ("tax_ready", "세금 부족/리스크 미리 방지"),
    ("evidence_clean", "증빙 누락 빠르게 정리"),
    ("faster_month_close", "월말 마감 시간을 줄이기"),
]
TAX_INDUSTRY_OPTIONS = [
    ("it", "IT/개발"),
    ("design", "디자인/영상"),
    ("marketing", "마케팅/광고"),
    ("creator", "창작/콘텐츠"),
    ("consulting", "강의/컨설팅"),
    ("retail", "도소매"),
    ("service", "서비스업"),
    ("other", "기타"),
    ("unknown", "모름"),
]
TAX_TYPE_OPTIONS = [
    ("general", "일반과세"),
    ("simple", "간이과세"),
    ("exempt", "면세"),
    ("unknown", "모름"),
]
PREV_INCOME_OPTIONS = [
    ("lt_30m", "3천만원 미만"),
    ("30m_80m", "3천만 ~ 8천만원"),
    ("80m_150m", "8천만 ~ 1억5천만원"),
    ("150m_300m", "1억5천만 ~ 3억원"),
    ("gt_300m", "3억원 이상"),
    ("unknown", "모름"),
]
WITHHOLDING_OPTIONS = [
    ("yes", "있음 (3.3%)"),
    ("no", "없음"),
    ("unknown", "모름"),
]
YES_NO_UNKNOWN_OPTIONS = [
    ("yes", "있음"),
    ("no", "없음"),
    ("unknown", "모름"),
]
OTHER_INCOME_TYPE_OPTIONS = [
    ("salary", "근로소득"),
    ("other", "기타소득"),
    ("interest_dividend", "이자/배당"),
    ("pension", "연금"),
]
HEALTH_INSURANCE_TYPE_OPTIONS = [
    ("employed", "직장가입자"),
    ("regional", "지역가입자"),
    ("dependent", "피부양자"),
    ("unknown", "모름"),
]
INCOME_CLASSIFICATION_OPTIONS = [
    ("business", "사업/프리랜서"),
    ("salary", "근로 중심"),
    ("mixed", "혼합"),
    ("other", "기타"),
    ("unknown", "모름"),
]
TAX_INDUSTRY_LABELS = {k: v for k, v in TAX_INDUSTRY_OPTIONS}
TAX_TYPE_LABELS = {k: v for k, v in TAX_TYPE_OPTIONS}
PREV_INCOME_LABELS = {k: v for k, v in PREV_INCOME_OPTIONS}
WITHHOLDING_LABELS = {k: v for k, v in WITHHOLDING_OPTIONS}
YES_NO_UNKNOWN_LABELS = {k: v for k, v in YES_NO_UNKNOWN_OPTIONS}
OTHER_INCOME_TYPE_LABELS = {k: v for k, v in OTHER_INCOME_TYPE_OPTIONS}
HEALTH_INSURANCE_TYPE_LABELS = {k: v for k, v in HEALTH_INSURANCE_TYPE_OPTIONS}
INCOME_CLASSIFICATION_LABELS = {k: v for k, v in INCOME_CLASSIFICATION_OPTIONS}
TAX_BASIC_STEP_ORDER = (
    "income_classification",
    "annual_gross_income_krw",
    "annual_deductible_expense_krw",
    "withheld_tax_annual_krw",
    "prepaid_tax_annual_krw",
)
TAX_BASIC_STEP_LABELS = {
    "income_classification": "소득 유형",
    "annual_gross_income_krw": "올해 총수입",
    "annual_deductible_expense_krw": "올해 업무 관련 지출",
    "withheld_tax_annual_krw": "올해 이미 떼인 세금",
    "prepaid_tax_annual_krw": "올해 이미 낸 세금",
}
ASSET_OTHER_INCOME_LABELS = {
    "salary": "근로",
    "interest": "이자",
    "dividend": "배당",
    "business": "사업/프리랜서",
    "other": "기타",
}
NHIS_FALLBACK_REASON_MESSAGES = {
    "feedback_unavailable": "즉시 피드백 계산 결과를 불러오지 못해 기본값으로 표시했어요.",
    "current_calc_failed": "현재 적용 월 보험료 계산을 완료하지 못했어요.",
    "november_calc_failed": "11월 반영 월 보험료 계산을 완료하지 못했어요.",
}

_ACCOUNT_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ACCOUNT_COLOR_PRESET = {
    "#DC2626",
    "#EA580C",
    "#CA8A04",
    "#16A34A",
    "#2563EB",
    "#1E3A8A",
    "#7C3AED",
}


def _nhis_fallback_reason_message(code: str) -> str:
    key = str(code or "").strip().lower()
    if not key:
        return "일부 계산을 완료하지 못해 임시 기준으로 보여주고 있어요."
    return NHIS_FALLBACK_REASON_MESSAGES.get(key, "일부 계산을 완료하지 못해 임시 기준으로 보여주고 있어요.")


def _get_or_create_settings(user_pk: int) -> Settings:
    st = Settings.query.filter_by(user_pk=user_pk).first()
    if st:
        return st
    st = Settings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
    db.session.add(st)
    db.session.flush()
    return st


def _mask_account_number(account_number: str | None) -> str:
    raw = (account_number or "").strip()
    if len(raw) <= 4:
        return raw
    return f"{'*' * (len(raw) - 4)}{raw[-4:]}"


def _normalize_account_color(raw: str | None) -> str:
    color = str(raw or "").strip().upper()
    if not color:
        return "#2563EB"
    if not color.startswith("#"):
        color = f"#{color}"
    if not _ACCOUNT_COLOR_RE.fullmatch(color):
        return "#2563EB"
    return color if color in _ACCOUNT_COLOR_PRESET else "#2563EB"


def _account_settings_redirect(anchor: str | None = None):
    base = url_for("web_profile.mypage")
    if anchor:
        return redirect(f"{base}#{anchor}")
    return redirect(base)


def _safe_next_url(raw: str | None) -> str:
    fallback = url_for("web_inbox.index")
    if not raw:
        return fallback
    raw = (raw or "").strip()
    if raw in {"", "None", "/None", "null", "/null", "undefined", "/undefined"}:
        return fallback
    try:
        u = urlparse(raw)
        if u.scheme or u.netloc:
            return fallback
        if not u.path.startswith("/"):
            return fallback
        return u.path + (f"?{u.query}" if u.query else "")
    except Exception:
        return fallback


def _parse_step(raw: str | None) -> int:
    try:
        s = int(raw or 1)
    except Exception:
        s = 1
    return max(1, min(s, 3))


def _parse_assets_step(raw: str | None, fallback: int = 1) -> int:
    try:
        s = int(raw or fallback)
    except Exception:
        s = int(fallback)
    return max(1, min(ASSET_QUIZ_TOTAL_STEPS, s))


def _parse_month_key(raw: str | None) -> str:
    s = (raw or "").strip()
    if len(s) == 7 and s[4] == "-":
        y, m = s.split("-", 1)
        try:
            yy = int(y)
            mm = int(m)
            if 2000 <= yy <= 2100 and 1 <= mm <= 12:
                return f"{yy:04d}-{mm:02d}"
        except Exception:
            pass
    return utcnow().strftime("%Y-%m")


def _maybe_record_profile_seasonal_landed(*, user_pk: int) -> None:
    landing_payload = seasonal_metric_payload_from_landing_args(request.args)
    if not landing_payload or str(landing_payload.get("cta_target") or "") != "profile":
        return
    if str(landing_payload.get("completion_action") or ""):
        active_metric = activate_pending_seasonal_card(session) or set_active_seasonal_card(session, landing_payload)
    else:
        clear_pending_seasonal_card(session)
        active_metric = landing_payload
    record_seasonal_card_event(
        user_pk=int(user_pk),
        event="seasonal_card_landed",
        route="web_profile.tax_profile",
        season_focus=str(active_metric.get("season_focus") or ""),
        card_type=str(active_metric.get("card_type") or ""),
        cta_target=str(active_metric.get("cta_target") or ""),
        source_screen=str(active_metric.get("source_screen") or "unknown"),
        priority=int(active_metric.get("priority") or 0),
        completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
        month_key=str(active_metric.get("month_key") or ""),
    )


def _maybe_complete_profile_seasonal_card(*, user_pk: int, route_name: str, extra: dict[str, Any] | None = None) -> None:
    active_metric = get_active_seasonal_card(session)
    if not active_metric or str(active_metric.get("completion_action") or "") != "tax_profile_saved":
        return
    month_key = _parse_month_key(str(active_metric.get("month_key") or ""))
    refreshed_experience = build_seasonal_experience(
        user_pk=int(user_pk),
        month_key=month_key,
        urls={},
    )
    record_seasonal_card_event(
        user_pk=int(user_pk),
        event="seasonal_card_completed",
        route=route_name,
        season_focus=str(active_metric.get("season_focus") or ""),
        card_type=str(active_metric.get("card_type") or ""),
        cta_target=str(active_metric.get("cta_target") or ""),
        source_screen=str(active_metric.get("source_screen") or "unknown"),
        priority=int(active_metric.get("priority") or 0),
        completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
        completion_state_after=(
            seasonal_card_completion_state(refreshed_experience, str(active_metric.get("card_type") or ""))
            or str(active_metric.get("completion_state_before") or "todo")
        ),
        month_key=month_key,
        extra=dict(extra or {}),
    )
    clear_active_seasonal_card(session)


def _tax_accuracy_level_from_required(required_inputs: dict[str, Any] | None) -> str:
    req = dict(required_inputs or {})
    if bool(req.get("exact_ready_inputs_ready")):
        return "exact_ready"
    if bool(req.get("high_confidence_inputs_ready")):
        return "high_confidence"
    high_missing = [str(v) for v in (req.get("high_confidence_missing_fields") or []) if str(v).strip()]
    if not high_missing:
        return "limited"
    if "income_classification" in high_missing:
        return "blocked"
    return "limited"


def _tax_reason_code_from_required(required_inputs: dict[str, Any] | None) -> str:
    req = dict(required_inputs or {})
    high_missing = [str(v) for v in (req.get("high_confidence_missing_fields") or []) if str(v).strip()]
    if "income_classification" in high_missing:
        return "missing_income_classification"
    if "annual_gross_income_krw" in high_missing:
        return "missing_taxable_income"
    if "annual_deductible_expense_krw" in high_missing:
        return "missing_taxable_income"
    if "withheld_tax_annual_krw" in high_missing:
        return "missing_withheld_tax"
    if "prepaid_tax_annual_krw" in high_missing:
        return "missing_prepaid_tax"
    if bool(req.get("exact_ready_inputs_ready")):
        return "ok"
    return "insufficient_profile_inputs"


def _tax_next_basic_missing_field(required_inputs: dict[str, Any] | None) -> str | None:
    req = dict(required_inputs or {})
    high_missing = {str(v).strip() for v in (req.get("high_confidence_missing_fields") or []) if str(v).strip()}
    for field in TAX_BASIC_STEP_ORDER:
        if field in high_missing:
            return field
    if "tax_basic_inputs_confirmed" in high_missing:
        return None
    return None


def _fallback_assets_context() -> dict:
    return {
        "profile": {
            "completed_at": None,
            "household_has_others": None,
            "dependents_count": None,
            "other_income_types": [],
            "other_income_annual_krw": None,
            "quiz_step": 1,
            "housing_mode": "unknown",
            "has_car": None,
        },
        "items": {
            "home": {"id": None, "kind": "home", "label": "부동산", "input": {}, "estimated": {}, "basis": {}, "warnings": []},
            "rent": {"id": None, "kind": "rent", "label": "전월세", "input": {}, "estimated": {}, "basis": {}, "warnings": []},
            "car": {"id": None, "kind": "car", "label": "차량", "input": {}, "estimated": {}, "basis": {}, "warnings": []},
        },
        "home_list": [],
        "car_list": [],
        "rent_list": [],
        "income_hybrid": {
            "enabled": False,
            "recommended_year": int(utcnow().year - 1),
            "active_year": int(utcnow().year - 1),
            "active_scope": "both",
            "input_basis": "income_amount_pre_tax",
            "is_pre_tax": True,
            "note": "",
            "fields": {},
            "year_options": [int(utcnow().year - 1)],
            "entry_count": 0,
            "updated_at": "",
        },
        "bill_history": [],
        "completion_ratio": 0,
        "missing_fields": [],
        "is_completed": False,
    }


def _fallback_assets_feedback(month_key: str, assets_ctx: dict | None = None, warning: str | None = None) -> dict:
    ctx = assets_ctx or _fallback_assets_context()
    year = (str(month_key or "")[:4] or utcnow().strftime("%Y"))
    nov_label = f"{year}-11"
    warn_list = [str(warning or "즉시 피드백 계산을 완료하지 못해 기본값으로 보여드려요.")]
    items = dict(ctx.get("items") or {})
    home_list = list(ctx.get("home_list") or [])
    car_list = list(ctx.get("car_list") or [])
    return {
        "current_nhis_est_krw": 0,
        "november_nhis_est_krw": 0,
        "november_diff_krw": 0,
        "tax_due_est_krw": 0,
        "completion_ratio": int(ctx.get("completion_ratio") or 0),
        "confidence": "low",
        "savings_effect_krw": 0,
        "note": "입력을 저장했어요. 추정은 잠시 후 다시 계산할게요.",
        "warnings": warn_list,
        "dataset_status": {
            "update_error": "feedback_unavailable",
            "is_stale": True,
            "used_fallback": True,
            "format_drift_keys": [],
        },
        "dataset": {"vehicle": {}, "home": {}},
        "items": {
            "home": dict(items.get("home") or {}),
            "car": dict(items.get("car") or {}),
            "rent": dict(items.get("rent") or {}),
            "home_list": home_list,
            "car_list": car_list,
        },
        "nhis_member_type_input": "unknown",
        "nhis_income_source": {
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "target_year": int(year) - 1,
            "used_year": None,
            "used_scope": None,
        },
        "nhis_income_override_values": {},
        "tax_income_source": {
            "source_code": "auto",
            "source_label": "자동 추정(연동)",
            "used_year": None,
            "target_year": int(year) - 1,
            "applied": False,
        },
        "derived_nhis_profile": {
            "target_month": month_key,
            "annual_income_krw": 0,
            "salary_monthly_krw": 0,
            "non_salary_annual_income_krw": 0,
            "rent_deposit_krw": 0,
            "rent_monthly_krw": 0,
        },
        "nhis_snapshot": {
            "effective_year": int(year),
            "health_insurance_rate": 0.0,
            "long_term_care_ratio_of_health": 0.0,
            "regional_point_value": 0.0,
            "income_reference_rule": "-",
            "fetched_at": None,
            "fetched_at_text": "-",
        },
        "nhis_estimate": {
            "member_type": "unknown",
            "mode": "failed",
            "confidence_level": "low",
            "income_premium_krw": 0,
            "property_premium_krw": 0,
            "health_est_krw": 0,
            "ltc_est_krw": 0,
            "total_est_krw": 0,
            "can_estimate": False,
            "income_points": 0.0,
            "property_points": 0.0,
            "income_year_applied": 0,
            "property_year_applied": 0,
            "notes": [],
            "warnings": [],
            "basis": {
                "source_year": None,
                "reference_last_checked_date": None,
            },
        },
        "nhis_november_estimate": {
            "member_type": "unknown",
            "mode": "failed",
            "confidence_level": "low",
            "income_premium_krw": 0,
            "property_premium_krw": 0,
            "health_est_krw": 0,
            "ltc_est_krw": 0,
            "total_est_krw": 0,
            "can_estimate": False,
            "income_points": 0.0,
            "property_points": 0.0,
            "income_year_applied": 0,
            "property_year_applied": 0,
            "notes": [],
            "warnings": [],
            "basis": {
                "source_year": None,
                "reference_last_checked_date": None,
            },
        },
        "nhis_compare": {
            "current_total_krw": 0,
            "november_total_krw": 0,
            "diff_krw": 0,
            "same_cycle_active": False,
            "fallback_used": True,
            "fallback_reason": "feedback_unavailable",
            "nov_calc_reused_current": False,
            "zero_diff_reason": f"11월 반영({nov_label}) 계산 결과를 불러오지 못해 기본값으로 보여줘요.",
            "scale_warning_current": False,
            "scale_warning_november": False,
            "current": {},
            "november": {},
        },
        "nhis_whatis_payload": {
            "ready": False,
            "error_message": "지금은 가정 계산을 할 수 없어요. 입력을 저장하면 정확도가 올라가요.",
            "debug_missing": ["feedback_unavailable"],
            "base": {
                "target_month": month_key,
                "member_type": "unknown",
                "salary_monthly_krw": 0,
                "annual_fin_income_krw": 0,
                "rent_deposit_krw": 0,
                "rent_monthly_krw": 0,
                "property_tax_base_total_krw": 0,
                "owned_home_rent_eval_krw": 0,
                "income_premium_krw": 0,
                "property_premium_krw": 0,
                "health_est_krw": 0,
                "ltc_est_krw": 0,
                "total_est_krw": 0,
                "property_base_after_deduction_krw": 0,
                "property_points": 0.0,
            },
            "rules": {
                "health_insurance_rate": 0.0,
                "regional_point_value": 0.0,
                "long_term_care_ratio_of_health": 0.0,
                "health_premium_floor_krw": 0,
                "health_premium_cap_krw": 0,
                "property_basic_deduction_krw": 100000000,
                "rent_eval_multiplier": 0.30,
                "rent_month_to_deposit_multiplier": 40,
                "property_points_table": [],
                "property_points_table_loaded": False,
                "rules_version": "fallback",
            },
            "ui_flags": {
                "show_fin_to_10m": False,
                "show_monthly_rent_delta": False,
                "housing_mode": str((ctx.get("profile") or {}).get("housing_mode") or "unknown"),
            },
            "notes": ["금융소득 합계가 1,000만 전후면 반영 방식이 달라질 수 있어요(추정)."],
            "flags": {
                "asset_rent_overlap_unknown": False,
                "asset_property_unknown": False,
                "asset_current_rent_unknown": False,
            },
        },
    }


def _normalize_assets_feedback(month_key: str, assets_ctx: dict | None, raw_feedback: dict | None) -> dict:
    def _as_dict(raw: Any) -> dict[str, Any]:
        return dict(raw) if isinstance(raw, dict) else {}

    base = _fallback_assets_feedback(month_key=month_key, assets_ctx=assets_ctx)
    if not isinstance(raw_feedback, dict):
        return base

    merged = dict(base)
    merged.update(raw_feedback)

    base_items = dict(base.get("items") or {})
    merged_items = dict(merged.get("items") or {})
    merged["items"] = {
        "home": dict(base_items.get("home") or {}),
        "car": dict(base_items.get("car") or {}),
        "rent": dict(base_items.get("rent") or {}),
        "home_list": list(base_items.get("home_list") or []),
        "car_list": list(base_items.get("car_list") or []),
    }
    merged["items"].update(merged_items)

    merged["nhis_snapshot"] = {
        **_as_dict(base.get("nhis_snapshot")),
        **_as_dict(merged.get("nhis_snapshot")),
    }
    fetched_at_text = str((merged["nhis_snapshot"] or {}).get("fetched_at_text") or "").strip()
    if not fetched_at_text:
        merged["nhis_snapshot"]["fetched_at_text"] = "-"

    merged["nhis_compare"] = {
        **_as_dict(base.get("nhis_compare")),
        **_as_dict(merged.get("nhis_compare")),
    }
    compare = dict(merged.get("nhis_compare") or {})
    fallback_reasons = list(compare.get("fallback_reasons") or [])
    if not fallback_reasons:
        fallback_reason_text = str(compare.get("fallback_reason") or "").strip()
        if fallback_reason_text:
            fallback_reasons = [p.strip() for p in fallback_reason_text.split("+") if str(p).strip()]
    fallback_reasons = list(dict.fromkeys([str(code).strip().lower() for code in fallback_reasons if str(code).strip()]))
    compare["fallback_reasons"] = fallback_reasons
    fallback_reason_messages = [_nhis_fallback_reason_message(code) for code in fallback_reasons]
    if bool(compare.get("fallback_used")) and not fallback_reason_messages:
        fallback_reason_messages = [_nhis_fallback_reason_message("")]
    compare["fallback_reason_messages"] = list(dict.fromkeys([msg for msg in fallback_reason_messages if str(msg).strip()]))
    merged["nhis_compare"] = compare
    merged["nhis_estimate"] = {
        **_as_dict(base.get("nhis_estimate")),
        **_as_dict(merged.get("nhis_estimate")),
    }
    merged["nhis_november_estimate"] = {
        **_as_dict(base.get("nhis_november_estimate")),
        **_as_dict(merged.get("nhis_november_estimate")),
    }
    merged["derived_nhis_profile"] = {
        **_as_dict(base.get("derived_nhis_profile")),
        **_as_dict(merged.get("derived_nhis_profile")),
    }

    merged["warnings"] = list(merged.get("warnings") or [])
    merged["current_nhis_est_krw"] = int(merged.get("current_nhis_est_krw") or 0)
    merged["november_nhis_est_krw"] = int(merged.get("november_nhis_est_krw") or 0)
    merged["november_diff_krw"] = int(merged.get("november_diff_krw") or 0)
    merged["tax_due_est_krw"] = int(merged.get("tax_due_est_krw") or 0)
    merged["completion_ratio"] = int(merged.get("completion_ratio") or 0)
    merged["note"] = str(merged.get("note") or "")
    return merged


def _build_nhis_what_if_cards(profile: dict, snapshot_obj, current_total_krw: int) -> list[dict]:
    scenarios: list[dict] = []

    def _append_case(title: str, desc: str, changed: dict) -> None:
        base = dict(profile or {})
        base.update(changed or {})
        try:
            out = estimate_nhis_current_vs_november(base, snapshot_obj)
            total = int(out.get("current_total_krw") or 0)
        except Exception:
            total = int(current_total_krw or 0)
        saved = max(0, int(current_total_krw or 0) - total)
        scenarios.append(
            {
                "title": title,
                "desc": desc,
                "after_total_krw": max(0, total),
                "saved_krw": saved,
            }
        )

    if bool(profile.get("household_has_others") is True):
        _append_case(
            "세대 합산이 아닌 경우",
            "실제 독립 거주/생계 요건을 충족하는 경우에만 해당돼요(추정).",
            {"household_has_others": False},
        )

    non_salary = 0
    try:
        non_salary = int(profile.get("non_salary_annual_income_krw") or 0)
    except Exception:
        non_salary = 0
    if non_salary > 0:
        _append_case(
            "보수 외 소득이 줄어든 경우",
            "휴업/소득 급감 등 실제 변동이 있는 경우 공단 조정 가능성을 확인해보세요(추정).",
            {"non_salary_annual_income_krw": max(0, int(round(non_salary * 0.5)))},
        )

    prop = 0
    try:
        prop = int(profile.get("property_tax_base_total_krw") or 0)
    except Exception:
        prop = 0
    if prop > 0:
        _append_case(
            "재산 반영 금액이 낮아진 경우",
            "재산 공제/부채공제 요건 충족 시 달라질 수 있어요(추정).",
            {"property_tax_base_total_krw": max(0, int(round(prop * 0.7)))},
        )

    return scenarios[:3]


@web_profile_bp.route("/mypage", methods=["GET"])
@login_required
def mypage_legacy():
    return redirect(url_for("web_profile.mypage"), code=302)


@web_profile_bp.route("/dashboard/account", methods=["GET", "POST"])
@login_required
def mypage():
    user_pk = int(session["user_id"])
    user = User.query.filter_by(id=user_pk).first()
    if not user:
        session.clear()
        return redirect(url_for("web_auth.login"))

    st = _get_or_create_settings(user_pk)
    meta = get_onboarding_meta(user_pk)
    profile = get_tax_profile(user_pk)

    if request.method == "POST":
        freelancer_type = safe_str(request.form.get("freelancer_type"), max_len=40)
        monthly_income_band = safe_str(request.form.get("monthly_income_band"), max_len=40)
        work_mode = safe_str(request.form.get("work_mode"), max_len=40)
        primary_goal = safe_str(request.form.get("primary_goal"), max_len=40)

        ok, msg = save_onboarding(
            user_pk=user_pk,
            freelancer_type=freelancer_type,
            monthly_income_band=monthly_income_band,
            work_mode=work_mode,
            primary_goal=primary_goal,
        )
        if not ok:
            flash(msg, "error")
            return redirect(url_for("web_profile.mypage"))

        # 기본 세율(온보딩과 별개, 마이페이지에서 함께 관리)
        try:
            tax_rate = float(safe_str(request.form.get("default_tax_rate"), max_len=16) or 0.15)
        except Exception:
            tax_rate = 0.15
        tax_rate = max(0.05, min(0.40, tax_rate))
        st.default_tax_rate = tax_rate

        nhi_monthly_krw = parse_int_krw(request.form.get("nhi_monthly_krw")) or 0
        st.nhi_monthly_krw = clamp_int(nhi_monthly_krw, minimum=0, maximum=1_000_000_000_000)
        month_end_reminder_enabled = parse_bool_yn(request.form.get("month_end_reminder_enabled")) is True
        st.month_end_reminder_enabled = month_end_reminder_enabled
        if month_end_reminder_enabled:
            session.pop("month_end_banner_hidden_month", None)
            session.modified = True

        db.session.add(st)
        db.session.commit()

        flash("계정 정보가 저장되었습니다.", "success")
        return redirect(url_for("web_profile.mypage"))

    # 기본 설정 카드에서 표시하는 건보료는 Settings 값을 우선 사용하고,
    # 비어 있으면 세금 프로필 값으로 폴백한다.
    settings_nhi_monthly = 0
    try:
        settings_nhi_monthly = int(getattr(st, "nhi_monthly_krw", 0) or 0)
    except Exception:
        settings_nhi_monthly = 0
    profile_nhi_monthly = 0
    try:
        profile_nhi_monthly = int((profile or {}).get("health_insurance_monthly_krw") or 0)
    except Exception:
        profile_nhi_monthly = 0
    account_nhi_monthly_value = settings_nhi_monthly if settings_nhi_monthly > 0 else max(0, profile_nhi_monthly)

    links = (
        BankAccountLink.query.filter(BankAccountLink.user_pk == user_pk)
        .order_by(BankAccountLink.created_at.desc())
        .all()
    )
    linked_accounts = [
        {
            "bank_code": (link.bank_code or "").strip(),
            "account_number_masked": _mask_account_number(link.account_number),
            "alias": (link.alias or "").strip(),
            "is_active": bool(link.is_active),
            "last_synced_at": link.last_synced_at,
        }
        for link in links
    ]

    managed_accounts = list_accounts_for_ui(user_pk, include_hidden=True)
    managed_visible_count = sum(1 for row in managed_accounts if not bool(row.get("is_hidden")))
    managed_hidden_count = sum(1 for row in managed_accounts if bool(row.get("is_hidden")))
    merge_undo_available = False
    merge_undo_log_id = None
    try:
        last_merge_log = (
            ActionLog.query.filter(ActionLog.user_pk == int(user_pk))
            .filter(ActionLog.action_type == "bulk_update")
            .filter(ActionLog.is_reverted.is_(False))
            .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
            .first()
        )
        if last_merge_log and isinstance(last_merge_log.before_state, dict):
            payload = last_merge_log.before_state.get("payload")
            if isinstance(payload, dict) and str(payload.get("kind") or "") == "account_merge":
                merge_undo_available = True
                merge_undo_log_id = int(last_merge_log.id)
    except Exception:
        db.session.rollback()

    ent = get_user_entitlements(user)
    linked_active_count = sum(1 for item in linked_accounts if item["is_active"])
    return render_template(
        "mypage.html",
        user=user,
        current_plan=ent.plan_code,
        current_plan_label=plan_label_ko(ent.plan_code),
        plan_status=ent.plan_status,
        included_account_limit=int(ent.included_account_limit),
        extra_account_slots=int(ent.extra_account_slots),
        max_linked_accounts=int(ent.max_linked_accounts),
        linked_accounts_active_count=linked_active_count,
        can_bank_link=bool(ent.can_bank_link),
        can_package_download=bool(ent.can_package_download),
        sync_interval_minutes=ent.sync_interval_minutes,
        st=st,
        meta=meta,
        freelancer_types=FREELANCER_TYPES,
        income_bands=INCOME_BANDS,
        work_modes=WORK_MODES,
        primary_goals=PRIMARY_GOALS,
        account_nhi_monthly_value=account_nhi_monthly_value,
        linked_accounts=linked_accounts,
        linked_accounts_count=len(linked_accounts),
        managed_accounts=managed_accounts,
        managed_visible_count=managed_visible_count,
        managed_hidden_count=managed_hidden_count,
        merge_undo_available=merge_undo_available,
        merge_undo_log_id=merge_undo_log_id,
    )


@web_profile_bp.post("/dashboard/account/plan")
@login_required
def change_plan():
    flash("플랜 변경은 결제/운영 처리로만 변경할 수 있어요.", "error")
    return redirect(f"{url_for('web_profile.mypage')}#plan")


@web_profile_bp.post("/dashboard/account/bank/update")
@login_required
def account_bank_update():
    user_pk = int(session["user_id"])
    try:
        account_id = int(request.form.get("account_id") or 0)
    except Exception:
        account_id = 0
    if account_id <= 0:
        flash("수정할 계좌를 다시 선택해 주세요.", "error")
        return _account_settings_redirect("bank-management")

    row = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.id == int(account_id))
        .first()
    )
    if not row:
        flash("계좌를 찾을 수 없어요.", "error")
        return _account_settings_redirect("bank-management")

    alias = safe_str(request.form.get("alias"), max_len=64)
    color_hex = _normalize_account_color(request.form.get("color_hex"))
    try:
        row.alias = alias or None
        row.color_hex = color_hex
        db.session.add(row)
        db.session.commit()
        flash("계좌 정보를 저장했어요.", "success")
    except Exception:
        db.session.rollback()
        flash("계좌 저장에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
    return _account_settings_redirect("bank-management")


@web_profile_bp.post("/dashboard/account/bank/visibility")
@login_required
def account_bank_visibility():
    user_pk = int(session["user_id"])
    try:
        account_id = int(request.form.get("account_id") or 0)
    except Exception:
        account_id = 0
    action = safe_str(request.form.get("action"), max_len=16).lower()
    if account_id <= 0:
        flash("계좌를 다시 선택해 주세요.", "error")
        return _account_settings_redirect("bank-management")
    if action not in {"hide", "show"}:
        flash("요청을 처리할 수 없어요.", "error")
        return _account_settings_redirect("bank-management")

    exists = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.id == int(account_id))
        .first()
    )
    if not exists:
        flash("계좌를 찾을 수 없어요.", "error")
        return _account_settings_redirect("bank-management")

    try:
        set_account_hidden(int(user_pk), int(account_id), hidden=(action == "hide"))
        db.session.commit()
        flash("계좌 표시 설정을 저장했어요.", "success")
    except Exception:
        db.session.rollback()
        flash("설정을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
    return _account_settings_redirect("bank-management")


@web_profile_bp.post("/dashboard/account/bank/order")
@login_required
def account_bank_order():
    user_pk = int(session["user_id"])
    try:
        account_id = int(request.form.get("account_id") or 0)
    except Exception:
        account_id = 0
    direction = safe_str(request.form.get("direction"), max_len=12).lower()
    if account_id <= 0 or direction not in {"up", "down"}:
        flash("순서를 변경할 계좌를 다시 확인해 주세요.", "error")
        return _account_settings_redirect("bank-management")

    exists = (
        UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
        .filter(UserBankAccount.id == int(account_id))
        .first()
    )
    if not exists:
        flash("계좌를 찾을 수 없어요.", "error")
        return _account_settings_redirect("bank-management")

    try:
        move_account_order(int(user_pk), int(account_id), direction=direction)
        db.session.commit()
        flash("계좌 순서를 변경했어요.", "success")
    except Exception:
        db.session.rollback()
        flash("순서 변경에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
    return _account_settings_redirect("bank-management")


@web_profile_bp.post("/dashboard/account/bank/merge")
@login_required
def account_bank_merge():
    user_pk = int(session["user_id"])
    try:
        from_account_id = int(request.form.get("from_account_id") or 0)
    except Exception:
        from_account_id = 0
    try:
        to_account_id = int(request.form.get("to_account_id") or 0)
    except Exception:
        to_account_id = 0

    ok, message, _payload = merge_user_bank_accounts(
        user_pk=int(user_pk),
        from_account_id=int(from_account_id),
        to_account_id=int(to_account_id),
        actor="account_settings",
    )
    flash(message, "success" if ok else "error")
    return _account_settings_redirect("bank-advanced")


@web_profile_bp.post("/dashboard/account/bank/merge-undo")
@login_required
def account_bank_merge_undo():
    user_pk = int(session["user_id"])
    try:
        undo_log_id = int(request.form.get("undo_log_id") or 0)
    except Exception:
        undo_log_id = 0
    ok, message = undo_last_account_merge(
        user_pk=int(user_pk),
        undo_log_id=(int(undo_log_id) if undo_log_id > 0 else None),
    )
    flash(message, "success" if ok else "error")
    return _account_settings_redirect("bank-advanced")


@web_profile_bp.route("/dashboard/profile", methods=["GET", "POST"])
@login_required
def tax_profile():
    user_pk = int(session["user_id"])
    safe_next = _safe_next_url(request.values.get("next"))
    recovery_source = safe_str(request.values.get("recovery_source"), max_len=80).lower()
    saved = (request.args.get("saved") or "").strip() == "1"
    return_to_next = (request.values.get("return_to_next") or "").strip() == "1"

    def _render(profile_data: dict, step: int, *, saved_flag: bool = False):
        other_income_types = profile_data.get("other_income_types") or []
        if not isinstance(other_income_types, list):
            other_income_types = []
        required_inputs = evaluate_tax_required_inputs(profile_data)
        if step == 2:
            level = _tax_accuracy_level_from_required(required_inputs)
            reason = _tax_reason_code_from_required(required_inputs)
            next_field_for_view = _tax_next_basic_missing_field(required_inputs)
            record_input_funnel_event(
                user_pk=user_pk,
                event="tax_basic_step_viewed",
                route="web_profile.tax_profile",
                screen="tax_profile_step2",
                accuracy_level_before=level,
                accuracy_level_after=level,
                reason_code_before=reason,
                reason_code_after=reason,
                extra={"step": int(step)},
            )
            record_input_funnel_event(
                user_pk=user_pk,
                event="tax_advanced_step_viewed",
                route="web_profile.tax_profile",
                screen="tax_profile_step2",
                accuracy_level_before=level,
                accuracy_level_after=level,
                reason_code_before=reason,
                reason_code_after=reason,
                extra={"step": int(step)},
            )
            if reason == "missing_income_classification":
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="tax_inline_income_classification_shown",
                    route="web_profile.tax_profile",
                    screen="tax_profile_step2",
                    accuracy_level_before=level,
                    accuracy_level_after=level,
                    reason_code_before=reason,
                    reason_code_after=reason,
                    extra={"step": int(step)},
                )
            inline_saved = safe_str(request.args.get("inline_saved"), max_len=40).lower()
            if inline_saved == "income_classification" and next_field_for_view and next_field_for_view != "income_classification":
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="tax_basic_next_step_viewed",
                    route="web_profile.tax_profile",
                    screen="tax_profile_step2",
                    accuracy_level_before=level,
                    accuracy_level_after=level,
                    reason_code_before=reason,
                    reason_code_after=reason,
                    extra={
                        "step": int(step),
                        "next_field": next_field_for_view,
                        "from_inline": "income_classification",
                    },
                )
        tax_input_draft = build_tax_input_draft(user_pk=user_pk, profile=profile_data)
        draft_values = dict(tax_input_draft.get("draft_values") or {})
        prefill_basic = {
            "annual_gross_income_krw": (
                profile_data.get("annual_gross_income_krw")
                if profile_data.get("annual_gross_income_krw") is not None
                else draft_values.get("annual_gross_income_krw")
            ),
            "annual_deductible_expense_krw": (
                profile_data.get("annual_deductible_expense_krw")
                if profile_data.get("annual_deductible_expense_krw") is not None
                else draft_values.get("annual_deductible_expense_krw")
            ),
            "withheld_tax_annual_krw": (
                profile_data.get("withheld_tax_annual_krw")
                if profile_data.get("withheld_tax_annual_krw") is not None
                else draft_values.get("withheld_tax_annual_krw")
            ),
            "prepaid_tax_annual_krw": (
                profile_data.get("prepaid_tax_annual_krw")
                if profile_data.get("prepaid_tax_annual_krw") is not None
                else draft_values.get("prepaid_tax_annual_krw")
            ),
        }
        high_missing_fields = [str(v) for v in (required_inputs.get("high_confidence_missing_fields") or []) if str(v).strip()]
        missing_set = set(high_missing_fields)
        next_basic_field = _tax_next_basic_missing_field(required_inputs)
        current_step_index = 1
        for idx, key in enumerate(TAX_BASIC_STEP_ORDER, start=1):
            if key == next_basic_field:
                current_step_index = idx
                break
        done_count = int(sum(1 for key in TAX_BASIC_STEP_ORDER if key not in missing_set))
        stepwise_current_value = None
        if next_basic_field:
            stepwise_current_value = prefill_basic.get(next_basic_field)
            if stepwise_current_value is None and next_basic_field == "income_classification":
                current_income_class = str(profile_data.get("income_classification") or "unknown")
                if current_income_class in {"business", "salary", "mixed", "other"}:
                    stepwise_current_value = current_income_class
        stepwise_status = []
        for key in TAX_BASIC_STEP_ORDER:
            done = key not in missing_set
            stepwise_status.append(
                {
                    "key": key,
                    "label": TAX_BASIC_STEP_LABELS.get(key, key),
                    "done": bool(done),
                }
            )
        tax_stepwise = {
            "enabled": bool(step == 2),
            "next_field": next_basic_field,
            "next_label": TAX_BASIC_STEP_LABELS.get(next_basic_field or "", ""),
            "next_value": stepwise_current_value,
            "current_step_index": int(current_step_index),
            "total_steps": int(len(TAX_BASIC_STEP_ORDER)),
            "done_count": int(done_count),
            "status": stepwise_status,
        }
        advanced_prefill_taxable_income_annual_krw = None
        try:
            taxable_raw = profile_data.get("official_taxable_income_annual_krw")
            taxable_val = int(taxable_raw) if taxable_raw is not None and str(taxable_raw).strip() != "" else None
        except Exception:
            taxable_val = None
        if taxable_val is not None and taxable_val > 0:
            advanced_prefill_taxable_income_annual_krw = int(taxable_val)
        else:
            try:
                gross_val = int(prefill_basic.get("annual_gross_income_krw") or 0)
                expense_val = int(prefill_basic.get("annual_deductible_expense_krw") or 0)
            except Exception:
                gross_val = 0
                expense_val = 0
            if gross_val > 0:
                advanced_prefill_taxable_income_annual_krw = int(max(0, gross_val - expense_val))

        return render_template(
            "tax_profile.html",
            step=step,
            profile=profile_data,
            saved=saved_flag,
            next_url=safe_next,
            move_next_url=safe_next,
            return_to_next=return_to_next,
            completion_meta=tax_profile_completion_meta(user_pk),
            is_complete=tax_profile_is_complete(user_pk),
            industry_options=TAX_INDUSTRY_OPTIONS,
            tax_type_options=TAX_TYPE_OPTIONS,
            prev_income_options=PREV_INCOME_OPTIONS,
            withholding_options=WITHHOLDING_OPTIONS,
            yes_no_unknown_options=YES_NO_UNKNOWN_OPTIONS,
            other_income_type_options=OTHER_INCOME_TYPE_OPTIONS,
            health_insurance_type_options=HEALTH_INSURANCE_TYPE_OPTIONS,
            income_classification_options=INCOME_CLASSIFICATION_OPTIONS,
            required_inputs=required_inputs,
            tax_input_draft=tax_input_draft,
            tax_stepwise=tax_stepwise,
            prefill_basic=prefill_basic,
            advanced_prefill_taxable_income_annual_krw=advanced_prefill_taxable_income_annual_krw,
            selected_other_income_types=set(other_income_types),
            summary={
                "industry_group": TAX_INDUSTRY_LABELS.get(profile_data.get("industry_group"), "모름"),
                "industry_text": profile_data.get("industry_text") or "",
                "tax_type": TAX_TYPE_LABELS.get(profile_data.get("tax_type"), "모름"),
                "prev_income_band": PREV_INCOME_LABELS.get(profile_data.get("prev_income_band"), "모름"),
                "withholding_3_3": WITHHOLDING_LABELS.get(profile_data.get("withholding_3_3"), "모름"),
                "opening_date": (
                    "모름"
                    if (profile_data.get("opening_date") or "unknown") == "unknown"
                    else (profile_data.get("opening_date") or "-")
                ),
                "other_income": YES_NO_UNKNOWN_LABELS.get(profile_data.get("other_income"), "모름"),
                "other_income_types": [
                    OTHER_INCOME_TYPE_LABELS.get(code, code) for code in other_income_types
                ],
                "high_cost_asset": YES_NO_UNKNOWN_LABELS.get(profile_data.get("high_cost_asset"), "모름"),
                "labor_outsource": YES_NO_UNKNOWN_LABELS.get(profile_data.get("labor_outsource"), "모름"),
                "health_insurance_type": HEALTH_INSURANCE_TYPE_LABELS.get(
                    profile_data.get("health_insurance_type"), "모름"
                ),
                "health_insurance_monthly_krw": profile_data.get("health_insurance_monthly_krw"),
                "official_taxable_income_annual_krw": profile_data.get("official_taxable_income_annual_krw"),
                "annual_gross_income_krw": profile_data.get("annual_gross_income_krw"),
                "annual_deductible_expense_krw": profile_data.get("annual_deductible_expense_krw"),
                "withheld_tax_annual_krw": profile_data.get("withheld_tax_annual_krw"),
                "prepaid_tax_annual_krw": profile_data.get("prepaid_tax_annual_krw"),
                "income_classification": INCOME_CLASSIFICATION_LABELS.get(
                    profile_data.get("income_classification"), "모름"
                ),
                "tax_basic_inputs_confirmed": bool(profile_data.get("tax_basic_inputs_confirmed")),
                "tax_advanced_input_confirmed": bool(profile_data.get("tax_advanced_input_confirmed")),
            },
        )

    def _parse_optional_taxable_income_annual(raw_value: str | None) -> tuple[bool, int | None, str]:
        text = safe_str(raw_value, max_len=40).replace(",", "").replace("원", "").strip()
        if not text:
            return True, None, ""
        try:
            value = int(float(text))
        except Exception:
            return False, None, "연 과세표준은 숫자로 입력해 주세요."
        if value < 0:
            return False, None, "연 과세표준은 0 이상으로 입력해 주세요."
        return True, int(value), ""

    def _parse_optional_non_negative_amount(raw_value: str | None, field_label: str) -> tuple[bool, int | None, str]:
        text = safe_str(raw_value, max_len=40).replace(",", "").replace("원", "").strip()
        if not text:
            return True, None, ""
        try:
            value = int(float(text))
        except Exception:
            return False, None, f"{field_label}은 숫자로 입력해 주세요."
        if value < 0:
            return False, None, f"{field_label}은 0 이상으로 입력해 주세요."
        return True, int(value), ""

    profile = get_tax_profile(user_pk)
    step = _parse_step(request.values.get("step") or str(profile.get("wizard_last_step") or 1))
    if step > 1 and not tax_profile_is_complete(user_pk):
        step = 1

    if request.method == "GET":
        _maybe_record_profile_seasonal_landed(user_pk=int(user_pk))

    if request.method == "POST":
        step = _parse_step(request.form.get("step"))
        action = safe_str(request.form.get("action") or "next", max_len=20).lower()

        if step == 1:
            industry_group = safe_str(request.form.get("industry_group"), max_len=20) or "unknown"
            industry_text = safe_str(request.form.get("industry_text"), max_len=120)
            tax_type = safe_str(request.form.get("tax_type"), max_len=20) or "unknown"
            prev_income_band = safe_str(request.form.get("prev_income_band"), max_len=20) or "unknown"
            withholding_3_3 = safe_str(request.form.get("withholding_3_3"), max_len=20) or "unknown"
            ok, msg, payload = validate_tax_profile_input(
                industry_group=industry_group,
                industry_text=industry_text,
                tax_type=tax_type,
                prev_income_band=prev_income_band,
                withholding_3_3=withholding_3_3,
            )
            if not ok:
                flash(msg, "error")
                profile.update(
                    {
                        "industry_group": industry_group,
                        "industry_text": industry_text,
                        "tax_type": tax_type,
                        "prev_income_band": prev_income_band,
                        "withholding_3_3": withholding_3_3,
                    }
                )
                return _render(profile, 1, saved_flag=False)

            payload.update({"wizard_last_step": 2, "profile_flow_done": False})
            if "official_taxable_income_annual_krw" in request.form:
                ok_taxable, taxable_income_annual_krw, taxable_msg = _parse_optional_taxable_income_annual(
                    request.form.get("official_taxable_income_annual_krw")
                )
                if not ok_taxable:
                    flash(taxable_msg, "error")
                    return _render(profile, 1, saved_flag=False)
                payload["official_taxable_income_annual_krw"] = taxable_income_annual_krw
            ok2, msg2 = save_tax_profile(user_pk=user_pk, payload=payload)
            if not ok2:
                flash(msg2, "error")
                return redirect(url_for("web_profile.tax_profile", step=1, next=safe_next))
            _maybe_complete_profile_seasonal_card(
                user_pk=int(user_pk),
                route_name="web_profile.tax_profile",
                extra={"saved_step": 1},
            )

            return redirect(
                url_for(
                    "web_profile.tax_profile",
                    step=2,
                    next=safe_next,
                    return_to_next=1 if return_to_next else 0,
                )
            )

        if step == 2:
            before_required = evaluate_tax_required_inputs(profile)
            before_level = _tax_accuracy_level_from_required(before_required)
            before_reason = _tax_reason_code_from_required(before_required)
            industry_text = safe_str(request.form.get("industry_text"), max_len=120)
            opening_date = safe_str(request.form.get("opening_date"), max_len=10)
            opening_date_unknown = safe_str(request.form.get("opening_date_unknown"), max_len=4)
            other_income = safe_str(request.form.get("other_income"), max_len=20) or "unknown"
            other_income_types = [
                code
                for code in [safe_str(x, max_len=30) for x in request.form.getlist("other_income_types")]
                if code
            ]
            high_cost_asset = safe_str(request.form.get("high_cost_asset"), max_len=20) or "unknown"
            labor_outsource = safe_str(request.form.get("labor_outsource"), max_len=20) or "unknown"
            health_insurance_type = safe_str(request.form.get("health_insurance_type"), max_len=20) or "unknown"
            health_insurance_monthly_krw = safe_str(request.form.get("health_insurance_monthly_krw"), max_len=24)
            income_classification = safe_str(request.form.get("income_classification"), max_len=20).lower() or "unknown"
            if income_classification not in INCOME_CLASSIFICATION_LABELS:
                income_classification = "unknown"
            confirm_advanced_taxable_input = safe_str(
                request.form.get("confirm_advanced_taxable_input"),
                max_len=8,
            ) == "1"

            ok_taxable, taxable_income_annual_krw, taxable_msg = _parse_optional_taxable_income_annual(
                request.form.get("official_taxable_income_annual_krw")
            )
            if not ok_taxable:
                flash(taxable_msg, "error")
                return _render(profile, 2, saved_flag=False)
            if confirm_advanced_taxable_input and (taxable_income_annual_krw is None or int(taxable_income_annual_krw) <= 0):
                flash("고급 입력을 사용할 때는 연 과세표준을 0보다 큰 값으로 입력해 주세요.", "error")
                return _render(profile, 2, saved_flag=False)
            ok_gross, annual_gross_income_krw, gross_msg = _parse_optional_non_negative_amount(
                request.form.get("annual_gross_income_krw"),
                "연간 총수입",
            )
            if not ok_gross:
                flash(gross_msg, "error")
                return _render(profile, 2, saved_flag=False)
            ok_expense, annual_deductible_expense_krw, expense_msg = _parse_optional_non_negative_amount(
                request.form.get("annual_deductible_expense_krw"),
                "연간 필요경비",
            )
            if not ok_expense:
                flash(expense_msg, "error")
                return _render(profile, 2, saved_flag=False)
            ok_withheld, withheld_tax_annual_krw, withheld_msg = _parse_optional_non_negative_amount(
                request.form.get("withheld_tax_annual_krw"),
                "연간 원천징수세액",
            )
            if not ok_withheld:
                flash(withheld_msg, "error")
                return _render(profile, 2, saved_flag=False)
            ok_prepaid, prepaid_tax_annual_krw, prepaid_msg = _parse_optional_non_negative_amount(
                request.form.get("prepaid_tax_annual_krw"),
                "중간예납/기납부세액",
            )
            if not ok_prepaid:
                flash(prepaid_msg, "error")
                return _render(profile, 2, saved_flag=False)

            missing_basic_fields: list[str] = []
            if income_classification == "unknown":
                missing_basic_fields.append("income_classification")
            if annual_gross_income_krw is None:
                missing_basic_fields.append("annual_gross_income_krw")
            if annual_deductible_expense_krw is None:
                missing_basic_fields.append("annual_deductible_expense_krw")
            if withheld_tax_annual_krw is None:
                missing_basic_fields.append("withheld_tax_annual_krw")
            if prepaid_tax_annual_krw is None:
                missing_basic_fields.append("prepaid_tax_annual_krw")

            candidate_profile = dict(profile)
            existing_taxable = profile.get("official_taxable_income_annual_krw")
            try:
                existing_taxable = int(existing_taxable) if existing_taxable is not None else None
            except Exception:
                existing_taxable = None
            existing_advanced_confirmed = bool(profile.get("tax_advanced_input_confirmed")) and (
                existing_taxable is not None and int(existing_taxable) > 0
            )
            now_iso = utcnow().isoformat(timespec="seconds")
            candidate_profile.update(
                {
                    "industry_text": industry_text,
                    "opening_date": ("unknown" if opening_date_unknown == "1" else opening_date),
                    "other_income": other_income,
                    "other_income_types": other_income_types,
                    "high_cost_asset": high_cost_asset,
                    "labor_outsource": labor_outsource,
                    "health_insurance_type": health_insurance_type,
                    "health_insurance_monthly_krw": health_insurance_monthly_krw,
                    "official_taxable_income_annual_krw": (
                        taxable_income_annual_krw if confirm_advanced_taxable_input else existing_taxable
                    ),
                    "annual_gross_income_krw": annual_gross_income_krw,
                    "annual_deductible_expense_krw": annual_deductible_expense_krw,
                    "withheld_tax_annual_krw": withheld_tax_annual_krw,
                    "prepaid_tax_annual_krw": prepaid_tax_annual_krw,
                    "income_classification": income_classification,
                    "tax_basic_inputs_confirmed": bool(not missing_basic_fields),
                    "tax_basic_inputs_confirmed_at": (now_iso if not missing_basic_fields else None),
                    "tax_advanced_input_confirmed": (
                        bool(confirm_advanced_taxable_input and taxable_income_annual_krw is not None)
                        or bool(existing_advanced_confirmed)
                    ),
                    "tax_advanced_input_confirmed_at": (
                        now_iso
                        if (confirm_advanced_taxable_input and taxable_income_annual_krw is not None)
                        else profile.get("tax_advanced_input_confirmed_at")
                    ),
                }
            )
            if action == "skip":
                flash("세금 기본 입력 단계는 건너뛸 수 없어요.", "error")
                profile.update(candidate_profile)
                return _render(profile, 2, saved_flag=False)
            if missing_basic_fields:
                labels = {
                    "income_classification": "소득 유형",
                    "annual_gross_income_krw": "총수입",
                    "annual_deductible_expense_krw": "업무 관련 지출",
                    "withheld_tax_annual_krw": "이미 떼인 세금(원천징수)",
                    "prepaid_tax_annual_krw": "이미 낸 세금(기납부)",
                }
                missing_text = ", ".join(labels.get(k, k) for k in missing_basic_fields)
                flash(
                    f"기본 입력을 먼저 완료해 주세요: {missing_text} (원천징수/기납부가 없으면 0 입력)",
                    "error",
                )
                profile.update(candidate_profile)
                return _render(profile, 2, saved_flag=False)

            ok, msg, payload = validate_tax_profile_step2_input(
                opening_date=opening_date,
                opening_date_unknown=opening_date_unknown,
                other_income=other_income,
                other_income_types=other_income_types,
                high_cost_asset=high_cost_asset,
                labor_outsource=labor_outsource,
                health_insurance_type=health_insurance_type,
                health_insurance_monthly_krw=health_insurance_monthly_krw,
            )
            if not ok:
                flash(msg, "error")
                profile.update(
                    {
                        "industry_text": industry_text,
                        "opening_date": (
                            "unknown"
                            if opening_date_unknown == "1"
                            else opening_date
                        ),
                        "other_income": other_income,
                        "other_income_types": other_income_types,
                        "high_cost_asset": high_cost_asset,
                        "labor_outsource": labor_outsource,
                        "health_insurance_type": health_insurance_type,
                        "health_insurance_monthly_krw": health_insurance_monthly_krw,
                        "official_taxable_income_annual_krw": candidate_profile.get("official_taxable_income_annual_krw"),
                        "annual_gross_income_krw": annual_gross_income_krw,
                        "annual_deductible_expense_krw": annual_deductible_expense_krw,
                        "withheld_tax_annual_krw": withheld_tax_annual_krw,
                        "prepaid_tax_annual_krw": prepaid_tax_annual_krw,
                        "income_classification": income_classification,
                        "tax_basic_inputs_confirmed": candidate_profile.get("tax_basic_inputs_confirmed"),
                        "tax_basic_inputs_confirmed_at": candidate_profile.get("tax_basic_inputs_confirmed_at"),
                        "tax_advanced_input_confirmed": candidate_profile.get("tax_advanced_input_confirmed"),
                        "tax_advanced_input_confirmed_at": candidate_profile.get("tax_advanced_input_confirmed_at"),
                    }
                )
                return _render(profile, 2, saved_flag=False)

            payload.update(
                {
                    "industry_text": industry_text,
                    "wizard_last_step": 3,
                    "profile_flow_done": False,
                    "official_taxable_income_annual_krw": candidate_profile.get("official_taxable_income_annual_krw"),
                    "annual_gross_income_krw": annual_gross_income_krw,
                    "annual_deductible_expense_krw": annual_deductible_expense_krw,
                    "withheld_tax_annual_krw": withheld_tax_annual_krw,
                    "prepaid_tax_annual_krw": prepaid_tax_annual_krw,
                    "income_classification": income_classification,
                    "tax_basic_inputs_confirmed": candidate_profile.get("tax_basic_inputs_confirmed"),
                    "tax_basic_inputs_confirmed_at": candidate_profile.get("tax_basic_inputs_confirmed_at"),
                    "tax_advanced_input_confirmed": candidate_profile.get("tax_advanced_input_confirmed"),
                    "tax_advanced_input_confirmed_at": candidate_profile.get("tax_advanced_input_confirmed_at"),
                }
            )
            ok2, msg2 = save_tax_profile(user_pk=user_pk, payload=payload)
            if not ok2:
                flash(msg2, "error")
                return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next))

            after_profile = get_tax_profile(user_pk)
            after_required = evaluate_tax_required_inputs(after_profile)
            after_level = _tax_accuracy_level_from_required(after_required)
            after_reason = _tax_reason_code_from_required(after_required)
            record_input_funnel_event(
                user_pk=user_pk,
                event="tax_basic_step_saved",
                route="web_profile.tax_profile",
                screen="tax_profile_step2",
                accuracy_level_before=before_level,
                accuracy_level_after=after_level,
                reason_code_before=before_reason,
                reason_code_after=after_reason or before_reason,
                extra={
                    "step": 2,
                    "action": str(action or ""),
                    "recovery_source": recovery_source,
                },
            )
            if bool(confirm_advanced_taxable_input and taxable_income_annual_krw is not None and int(taxable_income_annual_krw) > 0):
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="tax_advanced_step_saved",
                    route="web_profile.tax_profile",
                    screen="tax_profile_step2",
                    accuracy_level_before=before_level,
                    accuracy_level_after=after_level,
                    reason_code_before=before_reason,
                    reason_code_after=after_reason or before_reason,
                    extra={"step": 2},
                )
            if after_level in {"high_confidence", "exact_ready"} and before_level in {"blocked", "limited"}:
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="tax_recovery_completed",
                    route="web_profile.tax_profile",
                    screen="tax_profile_step2",
                    accuracy_level_before=before_level,
                    accuracy_level_after=after_level,
                    reason_code_before=before_reason,
                    reason_code_after=after_reason or before_reason,
                    extra={"step": 2},
                )
            _maybe_complete_profile_seasonal_card(
                user_pk=int(user_pk),
                route_name="web_profile.tax_profile",
                extra={"saved_step": 2},
            )

            return redirect(
                url_for(
                    "web_profile.tax_profile",
                    step=3,
                    next=safe_next,
                    return_to_next=1 if return_to_next else 0,
                )
            )

        if step == 3 and action == "complete":
            if not tax_profile_is_complete(user_pk):
                flash("필수 항목 4개를 먼저 입력해 주세요. 모름 선택도 가능합니다.", "error")
                return redirect(
                    url_for(
                        "web_profile.tax_profile",
                        step=1,
                        next=safe_next,
                        return_to_next=1 if return_to_next else 0,
                    )
                )

            save_tax_profile(
                user_pk=user_pk,
                payload={
                    "wizard_last_step": 3,
                    "profile_flow_done": True,
                    "profile_completed_at": utcnow().isoformat(timespec="seconds"),
                },
            )
            flash("세금/기장 정보 입력이 완료되었습니다.", "success")
            if return_to_next and safe_next:
                return redirect(safe_next)
            return redirect(
                url_for(
                    "web_profile.tax_profile",
                    step=3,
                    saved=1,
                    next=safe_next,
                    return_to_next=1 if return_to_next else 0,
                )
            )

        return redirect(
            url_for(
                "web_profile.tax_profile",
                step=step,
                next=safe_next,
                return_to_next=1 if return_to_next else 0,
            )
        )

    tax_recovery_is_inline = recovery_source.startswith("tax_inline_") or recovery_source.endswith("_single_step")
    if recovery_source.startswith("tax_") and (not tax_recovery_is_inline):
        required_snapshot = evaluate_tax_required_inputs(profile)
        record_input_funnel_event(
            user_pk=user_pk,
            event="tax_recovery_cta_clicked",
            route="web_profile.tax_profile",
            screen="tax_profile",
            accuracy_level_before=_tax_accuracy_level_from_required(required_snapshot),
            accuracy_level_after=_tax_accuracy_level_from_required(required_snapshot),
            reason_code_before=_tax_reason_code_from_required(required_snapshot),
            reason_code_after=_tax_reason_code_from_required(required_snapshot),
            extra={"recovery_source": recovery_source},
        )
    return _render(profile, step, saved_flag=saved)


@web_profile_bp.post("/dashboard/profile/tax-income-classification")
@login_required
def tax_income_classification_quick_save():
    user_pk = int(session["user_id"])
    safe_next = _safe_next_url(request.form.get("next"))
    recovery_source = safe_str(request.form.get("recovery_source"), max_len=80).lower() or "tax_inline_income_classification"
    selected = safe_str(request.form.get("income_classification"), max_len=20).lower() or "unknown"
    if selected not in {"business", "salary", "mixed", "other"}:
        flash("소득 유형을 먼저 선택해 주세요.", "error")
        return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))

    before_profile = get_tax_profile(user_pk)
    before_required = evaluate_tax_required_inputs(before_profile)
    before_level = _tax_accuracy_level_from_required(before_required)
    before_reason = _tax_reason_code_from_required(before_required)

    ok, msg = save_tax_profile(
        user_pk=user_pk,
        payload={
            "income_classification": selected,
            "wizard_last_step": 2,
            "profile_flow_done": False,
        },
    )
    if not ok:
        flash(msg or "저장에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))

    after_profile = get_tax_profile(user_pk)
    after_required = evaluate_tax_required_inputs(after_profile)
    after_level = _tax_accuracy_level_from_required(after_required)
    after_reason = _tax_reason_code_from_required(after_required)
    next_field = _tax_next_basic_missing_field(after_required)

    record_input_funnel_event(
        user_pk=user_pk,
        event="tax_inline_income_classification_saved",
        route="web_profile.tax_income_classification_quick_save",
        screen="tax_income_classification_quick",
        accuracy_level_before=before_level,
        accuracy_level_after=after_level,
        reason_code_before=before_reason,
        reason_code_after=after_reason or before_reason,
        extra={
            "saved_field": "income_classification",
            "next_field": next_field or "",
        },
    )
    # Backward-compatible event for legacy funnel dashboards.
    record_input_funnel_event(
        user_pk=user_pk,
        event="tax_basic_step_saved",
        route="web_profile.tax_income_classification_quick_save",
        screen="tax_income_classification_quick",
        accuracy_level_before=before_level,
        accuracy_level_after=after_level,
        reason_code_before=before_reason,
        reason_code_after=after_reason or before_reason,
        extra={"saved_field": "income_classification"},
    )
    if after_level in {"high_confidence", "exact_ready"} and before_level in {"blocked", "limited"}:
        record_input_funnel_event(
            user_pk=user_pk,
            event="tax_recovery_completed",
            route="web_profile.tax_income_classification_quick_save",
            screen="tax_income_classification_quick",
            accuracy_level_before=before_level,
            accuracy_level_after=after_level,
            reason_code_before=before_reason,
            reason_code_after=after_reason or before_reason,
            extra={"saved_field": "income_classification"},
        )
    _maybe_complete_profile_seasonal_card(
        user_pk=int(user_pk),
        route_name="web_profile.tax_income_classification_quick_save",
        extra={"saved_field": "income_classification"},
    )

    flash("소득 유형을 저장했어요. 다음 입력을 이어서 완료해 주세요.", "success")
    return redirect(
        url_for(
            "web_profile.tax_profile",
            step=2,
            next=safe_next,
            recovery_source=recovery_source,
            focus=(next_field or ""),
            inline_saved="income_classification",
        )
    )


@web_profile_bp.post("/dashboard/profile/tax-basic-step")
@login_required
def tax_basic_step_save():
    user_pk = int(session["user_id"])
    safe_next = _safe_next_url(request.form.get("next"))
    recovery_source = safe_str(request.form.get("recovery_source"), max_len=80).lower() or "tax_stepwise"
    field = safe_str(request.form.get("field"), max_len=40)
    raw_value = request.form.get("value")

    if field not in TAX_BASIC_STEP_ORDER:
        flash("저장할 항목을 확인하지 못했어요. 다시 시도해 주세요.", "error")
        return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))

    before_profile = get_tax_profile(user_pk)
    before_required = evaluate_tax_required_inputs(before_profile)
    before_level = _tax_accuracy_level_from_required(before_required)
    before_reason = _tax_reason_code_from_required(before_required)

    payload: dict[str, Any] = {
        "wizard_last_step": 2,
        "profile_flow_done": False,
    }
    if field == "income_classification":
        selected = safe_str(raw_value, max_len=20).lower() or "unknown"
        if selected not in {"business", "salary", "mixed", "other"}:
            flash("소득 유형을 먼저 선택해 주세요.", "error")
            return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))
        payload["income_classification"] = selected
    else:
        text = safe_str(raw_value, max_len=40).replace(",", "").replace("원", "").strip()
        if text == "":
            flash(f"{TAX_BASIC_STEP_LABELS.get(field, field)} 값을 입력해 주세요. 0도 입력 가능해요.", "error")
            return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))
        try:
            parsed = int(float(text))
        except Exception:
            flash(f"{TAX_BASIC_STEP_LABELS.get(field, field)}은 숫자로 입력해 주세요.", "error")
            return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))
        if parsed < 0:
            flash(f"{TAX_BASIC_STEP_LABELS.get(field, field)}은 0 이상으로 입력해 주세요.", "error")
            return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))
        payload[field] = int(parsed)

    ok, msg = save_tax_profile(user_pk=user_pk, payload=payload)
    if not ok:
        flash(msg or "저장에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_profile.tax_profile", step=2, next=safe_next, recovery_source=recovery_source))

    interim_profile = get_tax_profile(user_pk)
    interim_required = evaluate_tax_required_inputs(interim_profile)
    core_missing = [
        key
        for key in (interim_required.get("high_confidence_missing_fields") or [])
        if str(key) in TAX_BASIC_STEP_ORDER
    ]
    if (not core_missing) and (not bool(interim_profile.get("tax_basic_inputs_confirmed"))):
        now_iso = utcnow().isoformat(timespec="seconds")
        save_tax_profile(
            user_pk=user_pk,
            payload={
                "tax_basic_inputs_confirmed": True,
                "tax_basic_inputs_confirmed_at": now_iso,
            },
        )

    after_profile = get_tax_profile(user_pk)
    after_required = evaluate_tax_required_inputs(after_profile)
    after_level = _tax_accuracy_level_from_required(after_required)
    after_reason = _tax_reason_code_from_required(after_required)
    next_field = _tax_next_basic_missing_field(after_required)

    record_input_funnel_event(
        user_pk=user_pk,
        event="tax_basic_step_saved",
        route="web_profile.tax_basic_step_save",
        screen="tax_profile_step2",
        accuracy_level_before=before_level,
        accuracy_level_after=after_level,
        reason_code_before=before_reason,
        reason_code_after=after_reason or before_reason,
        extra={
            "saved_field": field,
            "next_field": next_field or "",
            "recovery_source": recovery_source,
        },
    )
    if field == "income_classification":
        record_input_funnel_event(
            user_pk=user_pk,
            event="tax_inline_income_classification_saved",
            route="web_profile.tax_basic_step_save",
            screen="tax_profile_step2",
            accuracy_level_before=before_level,
            accuracy_level_after=after_level,
            reason_code_before=before_reason,
            reason_code_after=after_reason or before_reason,
            extra={
                "saved_field": field,
                "next_field": next_field or "",
                "recovery_source": recovery_source,
            },
        )
    else:
        record_input_funnel_event(
            user_pk=user_pk,
            event="tax_basic_next_step_saved",
            route="web_profile.tax_basic_step_save",
            screen="tax_profile_step2",
            accuracy_level_before=before_level,
            accuracy_level_after=after_level,
            reason_code_before=before_reason,
            reason_code_after=after_reason or before_reason,
            extra={
                "saved_field": field,
                "next_field": next_field or "",
                "recovery_source": recovery_source,
            },
        )
    if after_level in {"high_confidence", "exact_ready"} and before_level in {"blocked", "limited"}:
        record_input_funnel_event(
            user_pk=user_pk,
            event="tax_recovery_completed",
            route="web_profile.tax_basic_step_save",
            screen="tax_profile_step2",
            accuracy_level_before=before_level,
            accuracy_level_after=after_level,
            reason_code_before=before_reason,
            reason_code_after=after_reason or before_reason,
            extra={"saved_field": field},
        )
    _maybe_complete_profile_seasonal_card(
        user_pk=int(user_pk),
        route_name="web_profile.tax_basic_step_save",
        extra={"saved_field": field},
    )

    if next_field:
        flash(f"{TAX_BASIC_STEP_LABELS.get(field, field)} 저장 완료. 다음: {TAX_BASIC_STEP_LABELS.get(next_field, next_field)}", "success")
    else:
        flash("기본 입력 5단계를 모두 저장했어요.", "success")
    return redirect(
        url_for(
            "web_profile.tax_profile",
            step=2,
            next=safe_next,
            recovery_source=recovery_source,
            focus=(next_field or ""),
            inline_saved=("income_classification" if field == "income_classification" else ""),
        )
    )


@web_profile_bp.post("/dashboard/nhis/membership-type")
@login_required
def nhis_membership_type_quick_save():
    user_pk = int(session["user_id"])
    month_key = _parse_month_key(request.form.get("month") or request.form.get("target_month") or request.args.get("month"))
    safe_next = _safe_next_url(request.form.get("next"))
    recovery_source = safe_str(request.form.get("recovery_source"), max_len=80).lower() or "nhis_inline_membership_type"
    member_type = safe_str(request.form.get("member_type"), max_len=24).lower()

    ready_for_event = bool(check_nhis_ready().get("ready"))
    try:
        before_profile_evt = load_canonical_nhis_profile(
            user_pk=user_pk,
            month_key=(month_key or None),
            prefer_assets=False,
        )
    except Exception:
        db.session.rollback()
        before_profile_evt = nhis_profile_to_dict(None)
    before_meta_evt = build_nhis_result_meta(
        estimate={
            "member_type": str(before_profile_evt.get("member_type") or "unknown"),
            "mode": "insufficient",
            "confidence_level": "low",
            "can_estimate": False,
        },
        status={"is_stale": False, "update_error": "", "is_fallback_default": False},
        official_ready=ready_for_event,
        profile=before_profile_evt,
    )

    ok, msg = save_nhis_profile_from_form(
        user_pk=user_pk,
        form_data={"member_type": member_type, "target_month": month_key},
        allow_membership_only=True,
    )
    if not ok:
        flash(msg or "가입유형 저장에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(url_for("web_profile.nhis_page", month=month_key, source="nhis", recovery_source=recovery_source))

    try:
        after_profile_evt = load_canonical_nhis_profile(
            user_pk=user_pk,
            month_key=(month_key or None),
            prefer_assets=False,
        )
    except Exception:
        db.session.rollback()
        after_profile_evt = nhis_profile_to_dict(None)
    after_meta_evt = build_nhis_result_meta(
        estimate={
            "member_type": str(after_profile_evt.get("member_type") or "unknown"),
            "mode": "insufficient",
            "confidence_level": "low",
            "can_estimate": False,
        },
        status={"is_stale": False, "update_error": "", "is_fallback_default": False},
        official_ready=ready_for_event,
        profile=after_profile_evt,
    )

    before_level_evt = str(before_meta_evt.get("accuracy_level") or "limited")
    after_level_evt = str(after_meta_evt.get("accuracy_level") or "limited")
    reason_evt = str(after_meta_evt.get("reason") or before_meta_evt.get("reason") or "")
    record_input_funnel_event(
        user_pk=user_pk,
        event="nhis_inline_membership_type_saved",
        route="web_profile.nhis_membership_type_quick_save",
        screen="nhis_membership_quick",
        accuracy_level_before=before_level_evt,
        accuracy_level_after=after_level_evt,
        reason_code_before=str(before_meta_evt.get("reason") or ""),
        reason_code_after=reason_evt,
        extra={"saved_field": "member_type"},
    )
    # Backward-compatible event for legacy funnel dashboards.
    record_input_funnel_event(
        user_pk=user_pk,
        event="nhis_membership_step_saved",
        route="web_profile.nhis_membership_type_quick_save",
        screen="nhis_membership_quick",
        accuracy_level_before=before_level_evt,
        accuracy_level_after=after_level_evt,
        reason_code_before=str(before_meta_evt.get("reason") or ""),
        reason_code_after=reason_evt,
        extra={"saved_field": "member_type"},
    )
    if after_level_evt in {"high_confidence", "exact_ready"} and before_level_evt in {"blocked", "limited"}:
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_recovery_completed",
            route="web_profile.nhis_membership_type_quick_save",
            screen="nhis_membership_quick",
            accuracy_level_before=before_level_evt,
            accuracy_level_after=after_level_evt,
            reason_code_before=str(before_meta_evt.get("reason") or ""),
            reason_code_after=reason_evt,
            extra={"saved_field": "member_type"},
        )

    flash("가입유형을 저장했어요. 유형별 필수 입력을 이어서 완료해 주세요.", "success")
    return redirect(
        url_for(
            "web_profile.nhis_page",
            month=month_key,
            source="nhis",
            recovery_source=recovery_source,
            inline_saved="member_type",
        )
    )


@web_profile_bp.route("/dashboard/assets", methods=["GET", "POST"])
@login_required
def assets_page():
    user_pk = int(session["user_id"])
    user_row = User.query.filter_by(id=user_pk).first()
    requested_month = parse_date_ym(request.args.get("month") or request.form.get("month") or request.values.get("month"))
    month_key = _parse_month_key(requested_month)
    as_json = safe_str(request.args.get("format") or request.form.get("format"), max_len=16).lower() == "json"
    legacy_view = safe_str(request.args.get("legacy"), max_len=8).lower() in {"1", "true", "yes", "y"}

    if request.method == "GET" and (not as_json) and (not legacy_view):
        merged_url = url_for("web_profile.nhis_page", month=month_key, source="assets")
        return redirect(f"{merged_url}#asset-diagnosis")

    profile_row = get_or_create_asset_profile(user_pk)
    profile_dict = asset_profile_to_dict(profile_row)
    is_completed = bool(profile_dict.get("completed_at"))
    skip_quiz = safe_str(request.args.get("skip_quiz"), max_len=8).lower() in {"1", "true", "yes", "y"}

    if request.method == "GET" and (not is_completed) and (not skip_quiz):
        return redirect(
            url_for(
                "web_profile.assets_quiz",
                step=int(profile_dict.get("quiz_step") or 1),
                month=month_key,
            )
        )

    if request.method == "POST":
        ok, msg = save_assets_page(user_pk=user_pk, form=request.form, month_key=month_key)
        try:
            ctx = build_assets_context(user_pk, month_key=month_key)
        except Exception:
            db.session.rollback()
            ctx = _fallback_assets_context()
        try:
            feedback = build_assets_feedback(user_pk=user_pk, month_key=month_key)
        except Exception:
            db.session.rollback()
            feedback = _fallback_assets_feedback(
                month_key=month_key,
                assets_ctx=ctx,
                warning="즉시 피드백 계산을 완료하지 못했어요. 잠시 후 다시 확인해 주세요.",
            )
            if ok:
                msg = f"{msg} (추정 재계산은 잠시 후 다시 시도해 주세요.)"
        feedback = _normalize_assets_feedback(month_key=month_key, assets_ctx=ctx, raw_feedback=feedback)
        if ok:
            flash(msg, "success")
        else:
            safe_msg = with_retry_hint(
                to_user_message(
                    raw_message=msg,
                    fallback="저장에 실패했어요. 새로고침 후 다시 시도해 주세요.",
                )
            )
            flash(safe_msg, "error")

        if as_json:
            feedback_view = {
                "current_nhis_est_krw": int((feedback or {}).get("current_nhis_est_krw") or 0),
                "november_nhis_est_krw": int((feedback or {}).get("november_nhis_est_krw") or 0),
                "november_diff_krw": int((feedback or {}).get("november_diff_krw") or 0),
                "tax_due_est_krw": int((feedback or {}).get("tax_due_est_krw") or 0),
                "completion_ratio": int((feedback or {}).get("completion_ratio") or 0),
                "note": str((feedback or {}).get("note") or ""),
                "warnings": list((feedback or {}).get("warnings") or []),
                "nhis_estimate": {
                    "income_premium_krw": int(((feedback or {}).get("nhis_estimate") or {}).get("income_premium_krw") or 0),
                    "property_premium_krw": int(((feedback or {}).get("nhis_estimate") or {}).get("property_premium_krw") or 0),
                    "ltc_est_krw": int(((feedback or {}).get("nhis_estimate") or {}).get("ltc_est_krw") or 0),
                },
                "nhis_income_source": dict((feedback or {}).get("nhis_income_source") or {}),
                "tax_income_source": dict((feedback or {}).get("tax_income_source") or {}),
                "nhis_whatis_payload": dict((feedback or {}).get("nhis_whatis_payload") or {}),
            }
            return jsonify(
                {
                    "ok": bool(ok),
                    "message": msg,
                    "feedback": feedback_view,
                    "completion_ratio": int(ctx.get("completion_ratio") or 0),
                    "is_completed": bool(ctx.get("is_completed")),
                }
            ), (200 if ok else 400)

        refresh_ts = int(utcnow().timestamp() * 1000)
        return redirect(url_for("web_profile.nhis_page", month=month_key, source="nhis", _ts=refresh_ts))

    try:
        assets_ctx = build_assets_context(user_pk, month_key=month_key)
    except Exception:
        db.session.rollback()
        assets_ctx = _fallback_assets_context()
        flash("자산 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
    try:
        feedback = build_assets_feedback(user_pk=user_pk, month_key=month_key)
    except Exception:
        db.session.rollback()
        feedback = _fallback_assets_feedback(
            month_key=month_key,
            assets_ctx=assets_ctx,
            warning="즉시 피드백 계산을 완료하지 못했어요. 입력값은 유지돼요.",
        )
        flash("즉시 피드백 계산을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
    feedback = _normalize_assets_feedback(month_key=month_key, assets_ctx=assets_ctx, raw_feedback=feedback)
    nhis_compare = dict(feedback.get("nhis_compare") or {})
    current_est = dict(nhis_compare.get("current") or {})
    november_est = dict(nhis_compare.get("november") or {})
    debug_nhis = (request.args.get("debug_nhis") == "1") and (
        current_app.debug or is_admin_user(user_row)
    )
    def _safe_int_value(raw: object) -> int:
        try:
            return int(raw or 0)
        except Exception:
            return 0

    def _safe_float_value(raw: object) -> float:
        try:
            return float(raw or 0.0)
        except Exception:
            return 0.0

    def _debug_step_row(est: dict) -> dict:
        basis = dict(est.get("basis") or {})
        calc_steps = dict(basis.get("calc_steps") or {})
        return {
            "income_monthly_krw": _safe_int_value(calc_steps.get("income_monthly_krw_used") or est.get("income_monthly_evaluated_krw")),
            "income_premium_krw": _safe_int_value(calc_steps.get("income_premium_step1_krw") or est.get("income_premium_krw")),
            "rent_eval_krw": _safe_int_value(calc_steps.get("rent_eval_krw")),
            "property_deduction_krw": _safe_int_value(calc_steps.get("property_deduction_krw")),
            "property_base_after_deduction_krw": _safe_int_value(
                calc_steps.get("property_base_after_deduction_krw") or calc_steps.get("net_property_krw")
            ),
            "property_points": _safe_float_value(calc_steps.get("property_points_step2") or est.get("property_points")),
            "point_value_used": _safe_float_value(calc_steps.get("point_value_used") or est.get("point_value_used")),
            "property_premium_krw": _safe_int_value(calc_steps.get("property_premium_step3_krw") or est.get("property_premium_krw")),
            "health_premium_krw": _safe_int_value(calc_steps.get("health_premium_step4_krw") or est.get("health_est_krw")),
            "ltc_premium_krw": _safe_int_value(calc_steps.get("ltc_premium_step5_krw") or est.get("ltc_est_krw")),
            "total_krw": _safe_int_value(calc_steps.get("total_premium_step6_krw") or est.get("total_est_krw")),
            "income_year_applied": _safe_int_value(est.get("income_year_applied")),
            "property_year_applied": _safe_int_value(est.get("property_year_applied")),
            "duplication_suspected": bool(calc_steps.get("duplication_suspected")),
            "unit_scale_warning": bool(calc_steps.get("unit_scale_warning") or est.get("scale_warning")),
        }

    current_debug = _debug_step_row(current_est)
    november_debug = _debug_step_row(november_est)
    debug_payload = {
        "current": current_debug,
        "november": november_debug,
        "flags": {
            "unit_scale_warning": bool(current_debug.get("unit_scale_warning") or november_debug.get("unit_scale_warning")),
            "duplication_suspected": bool(
                current_debug.get("duplication_suspected") or november_debug.get("duplication_suspected")
            ),
            "fallback_used": bool(nhis_compare.get("fallback_used")),
        },
    }
    debug_payload["income_source"] = dict((feedback.get("nhis_income_source") or {}))
    debug_payload["income_override_values"] = dict((feedback.get("nhis_income_override_values") or {}))
    dataset_status = feedback.get("dataset_status") or {}
    dataset_warning = None
    if dataset_status.get("update_error"):
        if str(dataset_status.get("update_error") or "").strip().lower() == "format_drift_detected":
            dataset_warning = "공식 페이지 형식 변경을 감지해 최신 갱신을 건너뛰고, 마지막 검증 기준으로 추정했어요."
        else:
            dataset_warning = "자산 기준 데이터 업데이트에 실패해 마지막 기준으로 추정했어요."
    elif dataset_status.get("used_fallback"):
        dataset_warning = "자산 기준 데이터 준비 중이라 기본값으로 추정했어요."
    elif dataset_status.get("is_stale"):
        dataset_warning = "자산 기준 데이터가 오래되어 최신 확인이 필요해요."
    elif dataset_status.get("format_drift_keys"):
        drift_keys = [str(k) for k in (dataset_status.get("format_drift_keys") or []) if str(k).strip()]
        drift_text = ", ".join(drift_keys[:3])
        if drift_text:
            dataset_warning = f"공식 데이터 페이지 형식 변경을 감지했어요({drift_text}). 일부 항목은 보수적으로 추정합니다."
        else:
            dataset_warning = "공식 데이터 페이지 형식 변경을 감지했어요. 일부 항목은 보수적으로 추정합니다."

    return render_template(
        "assets.html",
        month_key=month_key,
        assets_ctx=assets_ctx,
        feedback=feedback,
        debug_nhis=debug_nhis,
        debug_payload=debug_payload,
        dataset_warning=dataset_warning,
        income_type_options=INCOME_TYPE_OPTIONS,
        income_type_labels=ASSET_OTHER_INCOME_LABELS,
    )


@web_profile_bp.route("/dashboard/assets/quiz", methods=["GET", "POST"])
@login_required
def assets_quiz():
    user_pk = int(session["user_id"])
    month_key = _parse_month_key(request.values.get("month"))
    profile_row = get_or_create_asset_profile(user_pk)

    if request.method == "POST":
        step = _parse_assets_step(request.form.get("step"), fallback=int(profile_row.quiz_step or 1))
        action = (request.form.get("action") or "next").strip().lower()

        if action == "back":
            # step4/step6은 텍스트 입력 중 Enter 제출 시 "이전"이 선택될 수 있어
            # 뒤로 가더라도 현재 입력을 먼저 보존한다.
            if step in {4, 6}:
                save_assets_quiz_step(
                    user_pk=user_pk,
                    step=step,
                    form=request.form,
                    month_key=month_key,
                )
            prev_step = max(1, step - 1)
            # 자가(own)인 경우 Step4(전월세)는 스킵 단계이므로
            # Step5에서 이전을 누르면 Step3으로 이동해야 자연스럽다.
            if step == 5 and str(profile_row.housing_mode or "unknown") == "own":
                prev_step = 3
            profile_row.quiz_step = prev_step
            profile_row.updated_at = utcnow()
            db.session.add(profile_row)
            db.session.commit()
            return redirect(url_for("web_profile.assets_quiz", step=prev_step, month=month_key))

        ok, msg, next_step = save_assets_quiz_step(
            user_pk=user_pk,
            step=step,
            form=request.form,
            month_key=month_key,
        )
        if ok:
            flash(msg, "success")
        else:
            flash(msg, "error")
            return redirect(url_for("web_profile.assets_quiz", step=step, month=month_key))

        if action == "skip":
            flash("좋아요. 이 단계는 건너뛰고 다음으로 갈게요.", "success")

        if step >= ASSET_QUIZ_TOTAL_STEPS:
            ratio, _missing, _completed = mark_assets_completed_if_ready(user_pk)
            profile_row = get_or_create_asset_profile(user_pk)
            if profile_row.completed_at is None:
                profile_row.completed_at = utcnow()
                db.session.add(profile_row)
                db.session.commit()
            flash(f"자산 진단이 완료됐어요. 정확도 {ratio}%로 계산해드릴게요.", "success")
            refresh_ts = int(utcnow().timestamp() * 1000)
            return redirect(url_for("web_profile.nhis_page", month=month_key, source="assets", _ts=refresh_ts))

        return redirect(url_for("web_profile.assets_quiz", step=next_step, month=month_key))

    step = _parse_assets_step(request.args.get("step"), fallback=int(profile_row.quiz_step or 1))
    if step == 4 and str(profile_row.housing_mode or "unknown") == "own":
        return redirect(url_for("web_profile.assets_quiz", step=5, month=month_key))
    feedback = build_assets_feedback(user_pk=user_pk, month_key=month_key)
    assets_ctx = build_assets_context(user_pk, month_key=month_key)
    profile = assets_ctx.get("profile") or {}
    items = assets_ctx.get("items") or {}

    return render_template(
        "assets_quiz.html",
        month_key=month_key,
        step=step,
        total_steps=ASSET_QUIZ_TOTAL_STEPS,
        progress_pct=int(round((step / ASSET_QUIZ_TOTAL_STEPS) * 100)),
        profile=profile,
        items=items,
        home_list=assets_ctx.get("home_list") or [],
        car_list=assets_ctx.get("car_list") or [],
        feedback=feedback,
        income_type_options=INCOME_TYPE_OPTIONS,
        income_type_labels=ASSET_OTHER_INCOME_LABELS,
    )


@web_profile_bp.get("/admin/assets-data")
@admin_required
def admin_assets_data():
    status = ensure_asset_datasets(refresh_if_stale_days=30, force_refresh=False)
    return render_template(
        "admin/assets_data.html",
        status=status,
    )


@web_profile_bp.get("/admin/nhis-rates")
@admin_required
def admin_nhis_rates():
    rows = (
        NhisRateSnapshot.query.order_by(NhisRateSnapshot.effective_year.desc(), NhisRateSnapshot.fetched_at.desc())
        .limit(12)
        .all()
    )
    return render_template("admin/nhis_rates.html", rows=rows)


@web_profile_bp.route("/dashboard/nhis", methods=["GET", "POST"])
@login_required
def nhis_page():
    user_pk = int(session["user_id"])
    user_row = User.query.filter_by(id=user_pk).first()
    recovery_source = safe_str(request.args.get("recovery_source") or request.form.get("recovery_source"), max_len=80).lower()
    source_arg = safe_str(request.args.get("source") or request.form.get("source"), max_len=16).lower()
    source_mode = "assets" if source_arg == "assets" else "nhis"
    save_feedback = {
        "saved": safe_str(request.args.get("saved"), max_len=8) in {"1", "true", "yes", "y"},
        "save_token": safe_str(request.args.get("save_token"), max_len=40),
    }
    requested_month = parse_date_ym(
        request.args.get("month")
        or request.form.get("target_month")
        or request.form.get("month")
        or request.values.get("month")
    )
    month_key = _parse_month_key(requested_month)

    if request.method == "POST":
        ready_for_event = bool(check_nhis_ready().get("ready"))
        try:
            before_profile_evt = load_canonical_nhis_profile(
                user_pk=user_pk,
                month_key=(month_key or None),
                prefer_assets=False,
            )
        except Exception:
            db.session.rollback()
            before_profile_evt = nhis_profile_to_dict(None)
        before_meta_evt = build_nhis_result_meta(
            estimate={
                "member_type": str(before_profile_evt.get("member_type") or "unknown"),
                "mode": "insufficient",
                "confidence_level": "low",
                "can_estimate": False,
            },
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=ready_for_event,
            profile=before_profile_evt,
        )

        action_raw = safe_str(request.form.get("action") or "save_main", max_len=64).lower()
        action_code = action_raw.split(":", 1)[0] if ":" in action_raw else action_raw
        payload = dict(request.form)
        payload["target_month"] = month_key
        payload["month"] = month_key

        ok_nhis, msg_nhis = True, ""
        if action_code == "save_membership_only":
            ok_nhis, msg_nhis = save_nhis_profile_from_form(
                user_pk=user_pk,
                form_data={
                    "member_type": payload.get("member_type"),
                    "target_month": month_key,
                },
                allow_membership_only=True,
            )
        else:
            ok_assets, msg_assets = save_assets_page(user_pk=user_pk, form=request.form, month_key=month_key)
            if not ok_assets:
                safe_msg_assets = with_retry_hint(
                    to_user_message(
                        raw_message=msg_assets,
                        fallback="저장에 실패했어요. 입력을 확인하고 다시 시도해 주세요.",
                    )
                )
                flash(safe_msg_assets, "error")
                return redirect(url_for("web_profile.nhis_page", month=month_key, source="nhis", retry="1"))

            asset_only_actions = {"update_item", "delete_item", "add_home_item", "add_car_item", "save_history_only"}
            if action_code not in asset_only_actions:
                payload_for_nhis = dict(payload)
                # 과거 고지 이력은 save_assets_page에서 이미 동기화하므로, 여기서는 중복 저장을 피한다.
                payload_for_nhis.pop("history_rows", None)
                for k in list(payload_for_nhis.keys()):
                    if str(k).startswith("history_"):
                        payload_for_nhis.pop(k, None)
                ok_nhis, msg_nhis = save_nhis_profile_from_form(user_pk=user_pk, form_data=payload_for_nhis)

        if ok_nhis:
            try:
                after_profile_evt = load_canonical_nhis_profile(
                    user_pk=user_pk,
                    month_key=(month_key or None),
                    prefer_assets=False,
                )
            except Exception:
                db.session.rollback()
                after_profile_evt = nhis_profile_to_dict(None)
            after_meta_evt = build_nhis_result_meta(
                estimate={
                    "member_type": str(after_profile_evt.get("member_type") or "unknown"),
                    "mode": "insufficient",
                    "confidence_level": "low",
                    "can_estimate": False,
                },
                status={"is_stale": False, "update_error": "", "is_fallback_default": False},
                official_ready=ready_for_event,
                profile=after_profile_evt,
            )
            saved_event = "nhis_detail_step_saved"
            if action_code in {"save_membership_only", "save_member_type"}:
                saved_event = "nhis_membership_step_saved"
            before_reason_evt = str(before_meta_evt.get("reason") or "")
            after_reason_evt = str(after_meta_evt.get("reason") or before_reason_evt or "")
            record_input_funnel_event(
                user_pk=user_pk,
                event=saved_event,
                route="web_profile.nhis_page",
                screen="nhis",
                accuracy_level_before=str(before_meta_evt.get("accuracy_level") or "limited"),
                accuracy_level_after=str(after_meta_evt.get("accuracy_level") or "limited"),
                reason_code_before=before_reason_evt,
                reason_code_after=after_reason_evt,
                extra={
                    "action": action_code,
                    "recovery_source": recovery_source,
                },
            )
            if action_code in {"save_membership_only", "save_member_type"}:
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="nhis_inline_membership_type_saved",
                    route="web_profile.nhis_page",
                    screen="nhis",
                    accuracy_level_before=str(before_meta_evt.get("accuracy_level") or "limited"),
                    accuracy_level_after=str(after_meta_evt.get("accuracy_level") or "limited"),
                    reason_code_before=before_reason_evt,
                    reason_code_after=after_reason_evt,
                    extra={"action": action_code, "recovery_source": recovery_source},
                )
            else:
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="nhis_detail_next_step_saved",
                    route="web_profile.nhis_page",
                    screen="nhis",
                    accuracy_level_before=str(before_meta_evt.get("accuracy_level") or "limited"),
                    accuracy_level_after=str(after_meta_evt.get("accuracy_level") or "limited"),
                    reason_code_before=before_reason_evt,
                    reason_code_after=after_reason_evt,
                    extra={"action": action_code, "recovery_source": recovery_source},
                )
            before_level_evt = str(before_meta_evt.get("accuracy_level") or "limited")
            after_level_evt = str(after_meta_evt.get("accuracy_level") or "limited")
            if after_level_evt in {"high_confidence", "exact_ready"} and before_level_evt in {"blocked", "limited"}:
                record_input_funnel_event(
                    user_pk=user_pk,
                    event="nhis_recovery_completed",
                    route="web_profile.nhis_page",
                    screen="nhis",
                    accuracy_level_before=before_level_evt,
                    accuracy_level_after=after_level_evt,
                    reason_code_before=before_reason_evt,
                    reason_code_after=after_reason_evt,
                    extra={"action": action_code},
                )
            flash("저장됐어요. 최신 입력 기준으로 다시 계산했어요.", "success")
        else:
            safe_msg_nhis = with_retry_hint(
                to_user_message(
                    raw_message=(msg_nhis or ""),
                    fallback="저장에 실패했어요. 새로고침 후 다시 시도해 주세요.",
                )
            )
            flash(safe_msg_nhis, "error")
        refresh_ts = int(utcnow().timestamp() * 1000)
        redirect_kwargs = {
            "month": month_key,
            "source": "nhis",
            "_ts": refresh_ts,
        }
        if ok_nhis:
            redirect_kwargs["saved"] = "1"
            redirect_kwargs["save_token"] = str(refresh_ts)
        else:
            redirect_kwargs["retry"] = "1"
        return redirect(url_for("web_profile.nhis_page", **redirect_kwargs))

    selected_month = month_key
    official_guard = get_official_guard_status()
    nhis_ready_status = check_nhis_ready(guard_status=official_guard)
    official_refs_valid = bool(nhis_ready_status.get("ready"))
    official_refs_message = str(
        nhis_ready_status.get("message")
        or official_guard.get("message")
        or ""
    ).strip()
    official_refs_reason = str(
        nhis_ready_status.get("reason")
        or official_guard.get("reason")
        or ""
    ).strip()
    prefer_assets_sync = source_mode == "assets"
    status = None
    status_message = None
    status_level = "muted"
    source_notice = (
        "현재는 자산 페이지에서 저장한 입력값을 우선 반영해 계산 중이에요."
        if prefer_assets_sync
        else "현재는 이 화면에서 입력한 값을 기준으로 계산 중이에요."
    )
    snapshot_display = snapshot_to_display_dict(None)
    profile = nhis_profile_to_dict(None)
    estimate = {
        "member_type": "unknown",
        "mode": "insufficient",
        "confidence_level": "low",
        "health_est_krw": 0,
        "ltc_est_krw": 0,
        "total_est_krw": 0,
        "notes": ["기준 데이터 준비 중입니다."],
        "warnings": [],
        "can_estimate": False,
    }
    compare = {
        "current_total_krw": 0,
        "november_total_krw": 0,
        "diff_krw": 0,
        "increase_krw": 0,
        "current": estimate,
        "november": estimate,
    }
    reason_breakdown = {
        "income": {"amount_krw": 0, "percent": 0},
        "property": {"amount_krw": 0, "percent": 0},
        "vehicle": {"amount_krw": 0, "percent": 0},
        "confidence": "low",
    }
    action_items: list[dict] = []
    what_if_cards: list[dict] = []
    basis_item = {"input": {}, "basis": {}, "warnings": []}
    debug_nhis = False
    debug_payload: dict = {}

    if official_refs_valid:
        try:
            status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6)
            snapshot_display = snapshot_to_display_dict(status.snapshot)
        except Exception:
            db.session.rollback()
            status_message = "기준 데이터 업데이트가 필요해요(개발용). 잠시 후 다시 시도해 주세요."
            status_level = "warn"

    format_warnings = []
    try:
        format_warnings = list((snapshot_display.get("sources_json") or {}).get("format_warnings") or [])
    except Exception:
        format_warnings = []

    try:
        profile = load_canonical_nhis_profile(
            user_pk=user_pk,
            month_key=(selected_month or None),
            prefer_assets=prefer_assets_sync,
        )
        profile["bill_history"] = list_nhis_bill_history(user_pk)
    except Exception:
        db.session.rollback()
        profile = nhis_profile_to_dict(None)

    profile_month = _parse_month_key(str(profile.get("target_month") or selected_month))
    selected_month = profile_month
    profile["target_month"] = selected_month

    if official_refs_valid:
        try:
            compare = estimate_nhis_current_vs_november(profile, (status.snapshot if status else None))
            estimate = dict(compare.get("current") or estimate)
            reason_breakdown = build_nhis_reason_breakdown(
                profile,
                (status.snapshot if status else None),
                int(compare.get("current_total_krw") or 0),
            )
            action_items = build_nhis_action_items(
                profile,
                (status.snapshot if status else None),
                int(compare.get("current_total_krw") or 0),
                int(compare.get("november_total_krw") or 0),
            )
            what_if_cards = _build_nhis_what_if_cards(
                profile,
                (status.snapshot if status else None),
                int(compare.get("current_total_krw") or 0),
            )
            basis_item = {
                "input": {
                    "가입유형": profile.get("member_type"),
                    "대상월": profile.get("target_month"),
                    "연소득(원)": profile.get("annual_income_krw"),
                    "재산세 과세표준(원)": profile.get("property_tax_base_total_krw"),
                    "보수 외 소득(원)": profile.get("non_salary_annual_income_krw"),
                },
                "basis": {
                    "source_name": str((estimate.get("basis") or {}).get("source_name") or "공식 공개자료 기반 건보료 기준"),
                    "source_year": (estimate.get("basis") or {}).get("source_year") or snapshot_display.get("effective_year"),
                    "fetched_at": (
                        snapshot_display.get("fetched_at").strftime("%Y-%m-%d %H:%M")
                        if snapshot_display.get("fetched_at")
                        else None
                    ),
                    "matched_key": f"mode={estimate.get('mode')}",
                    "calc_steps": {
                        "적용 소득연도": int(estimate.get("income_year_applied") or 0),
                        "적용 재산연도": int(estimate.get("property_year_applied") or 0),
                        "소득 평가월액(원)": int(estimate.get("income_monthly_evaluated_krw") or 0),
                        "소득 점수": round(float(estimate.get("income_points") or 0), 4),
                        "재산 반영금액(원)": int(estimate.get("property_amount_krw") or 0),
                        "재산 점수": round(float(estimate.get("property_points") or 0), 4),
                        "원건보료(원)": int(estimate.get("health_premium_raw_krw") or 0),
                        "상한 적용": "예" if (estimate.get("caps_applied") or []) else "아니오",
                        "하한 적용": "예" if (estimate.get("floors_applied") or []) else "아니오",
                        "현재 건보료(추정)": int(estimate.get("health_est_krw") or 0),
                        "현재 장기요양(추정)": int(estimate.get("ltc_est_krw") or 0),
                        "현재 합계(추정)": int(compare.get("current_total_krw") or 0),
                        "11월 합계(추정)": int(compare.get("november_total_krw") or 0),
                        "11월 차이(추정)": int(compare.get("diff_krw") or 0),
                    },
                    "confidence": estimate.get("confidence_level") or "low",
                    "note": str((estimate.get("basis") or {}).get("note") or "모든 결과는 추정치이며 실제 고지서와 차이가 있을 수 있어요."),
                },
                "warnings": list(estimate.get("warnings") or []),
            }
            debug_nhis = (request.args.get("debug_nhis") == "1") and (
                current_app.debug or is_admin_user(user_row)
            )
            if debug_nhis:
                debug_payload = {
                    "mode": estimate.get("mode"),
                    "member_type": estimate.get("member_type"),
                    "income_points": estimate.get("income_points"),
                    "property_points": estimate.get("property_points"),
                    "total_points": estimate.get("total_points"),
                    "point_value_used": estimate.get("point_value_used"),
                    "applied_floor": estimate.get("applied_floor"),
                    "applied_cap": estimate.get("applied_cap"),
                    "health_raw_krw": estimate.get("health_premium_raw_krw"),
                    "health_est_krw": estimate.get("health_est_krw"),
                    "ltc_est_krw": estimate.get("ltc_est_krw"),
                    "total_est_krw": estimate.get("total_est_krw"),
                    "caps_applied": estimate.get("caps_applied") or [],
                    "floors_applied": estimate.get("floors_applied") or [],
                    "income_year_applied": estimate.get("income_year_applied"),
                    "property_year_applied": estimate.get("property_year_applied"),
                    "cycle_start_year": estimate.get("cycle_start_year"),
                }
        except Exception:
            db.session.rollback()
            estimate = {
                "member_type": str(profile.get("member_type") or "unknown"),
                "mode": "failed",
                "confidence_level": "low",
                "health_est_krw": 0,
                "ltc_est_krw": 0,
                "total_est_krw": 0,
                "notes": ["계산에 실패했어요. 입력값을 확인해 주세요."],
                "warnings": [],
                "can_estimate": False,
            }
            compare = {
                "current_total_krw": int(estimate.get("total_est_krw") or 0),
                "november_total_krw": int(estimate.get("total_est_krw") or 0),
                "diff_krw": 0,
                "increase_krw": 0,
                "current": estimate,
                "november": estimate,
            }

    can_estimate = bool(estimate.get("can_estimate"))
    confidence = str(estimate.get("confidence_level") or "low")
    if confidence == "high":
        confidence_message = "고지서 기반이라 정확도가 높아요."
    elif confidence == "medium":
        confidence_message = "입력값 기준으로 거의 맞는 추정이에요."
    else:
        confidence_message = "입력 정보가 부족해요. 고지서를 입력하면 더 정확해져요."

    if status:
        if status.update_error:
            status_message = "기준 데이터 업데이트에 실패했어요. 마지막 기준으로 추정했어요."
            status_level = "warn"
        elif status.is_fallback_default:
            status_message = "기준 데이터 준비 중이라 기본 기준으로 추정했어요."
            status_level = "warn"
        elif status.is_stale:
            status_message = "기준 데이터가 오래되어 최신 확인이 필요해요."
            status_level = "warn"

    if format_warnings:
        format_msg = "공식 페이지 형식 변경을 감지했어요. 일부 값은 보수적으로 추정했어요."
        if status_message:
            status_message = f"{status_message} · {format_msg}"
        else:
            status_message = format_msg
        status_level = "warn"

    if not official_refs_valid:
        gate_msg = official_refs_message or "공식 기준 검증이 완료되지 않아 숫자를 표시할 수 없어요."
        if status_message:
            status_message = f"{status_message} · {gate_msg}"
        else:
            status_message = gate_msg
        status_level = "warn"

    nhis_status_payload = {
        "is_stale": bool(status.is_stale) if status else False,
        "update_error": str(status.update_error or "") if status else "",
        "is_fallback_default": bool(status.is_fallback_default) if status else False,
    }
    nhis_result_meta = build_nhis_result_meta(
        estimate=estimate,
        status=nhis_status_payload,
        official_ready=bool(official_refs_valid),
        profile=profile,
    )
    nhis_required_inputs = dict(
        nhis_result_meta.get("required_inputs")
        or evaluate_nhis_required_inputs(
            estimate=estimate,
            profile=profile,
            official_ready=bool(official_refs_valid),
        )
    )
    nhis_recovery_cta = build_nhis_recovery_cta(
        nhis_result_meta,
        recovery_url=f"{url_for('web_profile.nhis_page', month=selected_month, recovery_source='nhis_nhis_page_cta')}#asset-diagnosis",
    )
    current_nhis_level = str(nhis_result_meta.get("accuracy_level") or "limited")
    current_nhis_reason = str(nhis_result_meta.get("reason") or "")
    record_input_funnel_event(
        user_pk=user_pk,
        event="nhis_detail_step_viewed",
        route="web_profile.nhis_page",
        screen="nhis",
        accuracy_level_before=current_nhis_level,
        accuracy_level_after=current_nhis_level,
        reason_code_before=current_nhis_reason,
        reason_code_after=current_nhis_reason,
        extra={"month_key": selected_month},
    )
    if str(nhis_result_meta.get("reason") or "") == "missing_membership_type":
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_membership_step_viewed",
            route="web_profile.nhis_page",
            screen="nhis",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": selected_month},
        )
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_inline_membership_type_shown",
            route="web_profile.nhis_page",
            screen="nhis",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": selected_month},
        )
    inline_saved = safe_str(request.args.get("inline_saved"), max_len=40).lower()
    if inline_saved == "member_type":
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_detail_next_step_viewed",
            route="web_profile.nhis_page",
            screen="nhis",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": selected_month, "from_inline": "member_type"},
        )
    if nhis_recovery_cta.get("show"):
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_recovery_cta_shown",
            route="web_profile.nhis_page",
            screen="nhis",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"month_key": selected_month},
        )
    nhis_recovery_is_inline = recovery_source.startswith("nhis_inline_") or recovery_source.endswith("_single_step")
    if recovery_source.startswith("nhis_") and (not nhis_recovery_is_inline):
        record_input_funnel_event(
            user_pk=user_pk,
            event="nhis_recovery_cta_clicked",
            route="web_profile.nhis_page",
            screen="nhis",
            accuracy_level_before=current_nhis_level,
            accuracy_level_after=current_nhis_level,
            reason_code_before=current_nhis_reason,
            reason_code_after=current_nhis_reason,
            extra={"recovery_source": recovery_source},
        )
    if (not official_refs_valid) and official_refs_message:
        nhis_result_meta["detail"] = str(official_refs_message)

    try:
        assets_ctx = build_assets_context(user_pk, month_key=selected_month)
    except Exception:
        db.session.rollback()
        assets_ctx = _fallback_assets_context()
        flash("자산 입력 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
    try:
        feedback_raw = build_assets_feedback(user_pk=user_pk, month_key=selected_month)
    except Exception:
        db.session.rollback()
        feedback_raw = _fallback_assets_feedback(
            month_key=selected_month,
            assets_ctx=assets_ctx,
            warning="통합 피드백 계산을 완료하지 못했어요. 잠시 후 다시 확인해 주세요.",
        )
    feedback = _normalize_assets_feedback(month_key=selected_month, assets_ctx=assets_ctx, raw_feedback=feedback_raw)
    nhis_income_source = dict(feedback.get("nhis_income_source") or {})
    tax_income_source = dict(feedback.get("tax_income_source") or {})
    nhis_source_code = str(nhis_income_source.get("source_code") or "").strip().lower()
    tax_source_code = str(tax_income_source.get("source_code") or "").strip().lower()
    nhis_manual_applied = bool(nhis_income_source.get("applied")) or (nhis_source_code == "user_input")
    tax_manual_applied = bool(tax_income_source.get("applied")) or (tax_source_code == "user_input")
    if nhis_manual_applied and tax_manual_applied:
        source_badge = {
            "code": "user",
            "label": "사용자 입력(확정)",
            "css": "source-user",
            "detail": "건보/세금 모두 사용자 입력(확정) 기준",
        }
    elif nhis_manual_applied or tax_manual_applied:
        source_badge = {
            "code": "mixed",
            "label": "혼합",
            "css": "source-mixed",
            "detail": (
                "건보는 사용자 입력, 세금은 자동 추정 기준"
                if nhis_manual_applied and (not tax_manual_applied)
                else "세금은 사용자 입력, 건보는 자동 추정 기준"
            ),
        }
    else:
        source_badge = {
            "code": "auto",
            "label": "자동 추정",
            "css": "source-auto",
            "detail": "건보/세금 모두 자동 추정 기준",
        }

    dataset_status = feedback.get("dataset_status") or {}
    dataset_warning = None
    if dataset_status.get("update_error"):
        if str(dataset_status.get("update_error") or "").strip().lower() == "format_drift_detected":
            dataset_warning = "공식 페이지 형식 변경을 감지해 최신 갱신을 건너뛰고, 마지막 검증 기준으로 추정했어요."
        else:
            dataset_warning = "자산 기준 데이터 업데이트에 실패해 마지막 기준으로 추정했어요."
    elif dataset_status.get("used_fallback"):
        dataset_warning = "자산 기준 데이터 준비 중이라 기본값으로 추정했어요."
    elif dataset_status.get("is_stale"):
        dataset_warning = "자산 기준 데이터가 오래되어 최신 확인이 필요해요."

    return render_template(
        "nhis.html",
        profile=profile,
        estimate=estimate,
        compare=compare,
        reason_breakdown=reason_breakdown,
        action_items=action_items,
        what_if_cards=what_if_cards,
        basis_item=basis_item,
        debug_nhis=debug_nhis,
        debug_payload=debug_payload,
        snapshot=snapshot_display,
        selected_month=selected_month,
        can_estimate=can_estimate,
        confidence_message=confidence_message,
        status_message=status_message,
        status_level=status_level,
        source_mode=source_mode,
        source_notice=source_notice,
        month_key=selected_month,
        assets_ctx=assets_ctx,
        feedback=feedback,
        dataset_warning=dataset_warning,
        income_type_options=INCOME_TYPE_OPTIONS,
        income_type_labels=ASSET_OTHER_INCOME_LABELS,
        save_feedback=save_feedback,
        source_badge=source_badge,
        nhis_result_meta=nhis_result_meta,
        nhis_required_inputs=nhis_required_inputs,
        nhis_recovery_cta=nhis_recovery_cta,
        official_refs_valid=official_refs_valid,
        official_refs_message=official_refs_message,
        official_refs_reason=official_refs_reason,
        official_refs_last_checked=(
            nhis_ready_status.get("last_checked_at")
            or official_guard.get("last_checked_at")
            or ""
        ),
    )


@web_profile_bp.post("/dashboard/account/password")
@login_required
def change_password():
    user_pk = int(session["user_id"])

    ok, msg = change_user_password(
        user_pk=user_pk,
        current_password=(request.form.get("current_password") or ""),
        new_password=(request.form.get("new_password") or ""),
        new_password_confirm=(request.form.get("new_password_confirm") or ""),
    )
    if not ok:
        flash(msg, "error")
        audit_event("password_change_failed", user_pk=user_pk, outcome="denied", detail=msg)
        return redirect(f"{url_for('web_profile.mypage')}#security")

    session.clear()
    flash("비밀번호가 변경되었습니다. 보안을 위해 다시 로그인해 주세요.", "success")
    audit_event("password_change", user_pk=user_pk, outcome="ok")
    return redirect(url_for("web_auth.login", next=url_for("web_profile.mypage")))


@web_profile_bp.post("/dashboard/account/delete")
@login_required
def delete_account():
    user_pk = int(session["user_id"])

    ok, msg, file_delete_errors = delete_user_account(
        user_pk=user_pk,
        current_password=(request.form.get("delete_current_password") or ""),
        confirm_text=(request.form.get("confirm_text") or ""),
    )
    if not ok:
        flash(msg, "error")
        audit_event("account_delete_failed", user_pk=user_pk, outcome="denied", detail=msg)
        return redirect(f"{url_for('web_profile.mypage')}#danger-zone")

    session.clear()
    flash("계정이 삭제되었습니다. 그동안 이용해 주셔서 감사합니다.", "success")
    if file_delete_errors > 0:
        flash("일부 첨부 파일은 서버 정리 작업으로 이어서 제거됩니다.", "success")
    audit_event("account_delete", user_pk=user_pk, outcome="ok", detail=(f"file_cleanup_errors={file_delete_errors}" if file_delete_errors else ""))
    return redirect(url_for("web_main.landing"))
