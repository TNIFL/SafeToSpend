from __future__ import annotations

from typing import Any

from core.extensions import db
from services.accuracy_reason_codes import (
    NHIS_REASON_DATASET_FALLBACK,
    NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS,
    NHIS_REASON_MISSING_MEMBERSHIP_TYPE,
    NHIS_REASON_MISSING_NON_SALARY_INCOME,
    NHIS_REASON_MISSING_PROPERTY_TAX_BASE,
    NHIS_REASON_MISSING_SALARY_MONTHLY,
    NHIS_REASON_MISSING_SNAPSHOT,
    NHIS_REASON_OK,
    NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE,
    normalize_nhis_reason,
)
from services.health_insurance import get_monthly_health_insurance_buffer
from services.official_data_effects import collect_official_data_effects_for_user
from services.official_refs.guard import check_nhis_ready
from services.nhis_estimator import estimate_nhis_monthly_dict
from services.nhis_unified import load_canonical_nhis_profile
from services.nhis_rates import ensure_active_snapshot, snapshot_to_display_dict
from services.onboarding import get_tax_profile

NHIS_REQUIRED_FIELD_LABELS = {
    "member_type": "가입유형",
    "salary_monthly_krw": "직장 월 보수",
    "annual_income_krw": "연소득 총액",
    "non_salary_annual_income_krw": "보수 외 소득(연)",
    "property_tax_base_total_krw": "재산세 과세표준 합계",
    "financial_income_annual_krw": "금융소득(연)",
}


def _confidence_note(confidence: str) -> str:
    if confidence == "high":
        return "고지서 기반이라 정확도가 높아요."
    if confidence == "medium":
        return "고지서/입력값 기반 추정이에요."
    return "입력 정보가 부족해요. 고지서를 추가하면 더 정확해져요."


def _safe_int_or_none(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def evaluate_nhis_required_inputs(
    *,
    estimate: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    official_ready: bool,
) -> dict[str, Any]:
    est = dict(estimate or {})
    prof = dict(profile or {})
    allowed = {"regional", "employee", "dependent", "unknown"}
    raw_member_type = str(prof.get("member_type") or est.get("member_type") or "").strip().lower()
    member_type = str(est.get("member_type") or raw_member_type or "unknown").strip().lower()
    if not member_type:
        member_type = "unknown"

    bill_mode = bool(str(est.get("mode") or "").strip().lower().startswith("bill"))
    confidence = str(est.get("confidence_level") or "low").strip().lower()

    out: dict[str, Any] = {
        "member_type_input": raw_member_type or "unknown",
        "member_type_used": member_type,
        "official_ready": bool(official_ready),
        "high_confidence_inputs_ready": False,
        "exact_ready_inputs_ready": False,
        "high_confidence_missing_fields": [],
        "exact_ready_missing_fields": [],
        "blocked_reason": "",
        "limited_reason": "",
        "required_fields_by_member_type": {
            "regional": ["member_type", "annual_income_krw", "non_salary_annual_income_krw", "property_tax_base_total_krw"],
            "employee": ["member_type", "salary_monthly_krw", "non_salary_annual_income_krw"],
            "dependent": ["member_type"],
        },
    }

    if not bool(official_ready):
        out["blocked_reason"] = NHIS_REASON_MISSING_SNAPSHOT
        return out

    if not raw_member_type:
        out["blocked_reason"] = NHIS_REASON_MISSING_MEMBERSHIP_TYPE
        return out
    if raw_member_type not in allowed:
        out["blocked_reason"] = NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE
        return out
    if member_type not in allowed:
        out["blocked_reason"] = NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE
        return out
    if member_type == "unknown":
        out["blocked_reason"] = NHIS_REASON_MISSING_MEMBERSHIP_TYPE
        return out

    if member_type == "employee":
        salary_monthly = _safe_int_or_none(prof.get("salary_monthly_krw"))
        non_salary_annual = _safe_int_or_none(prof.get("non_salary_annual_income_krw"))
        high_missing: list[str] = []
        if (salary_monthly or 0) <= 0:
            high_missing.append("salary_monthly_krw")
        if non_salary_annual is None:
            high_missing.append("non_salary_annual_income_krw")
        out["high_confidence_missing_fields"] = high_missing
        out["exact_ready_missing_fields"] = list(high_missing)
        out["high_confidence_inputs_ready"] = bool(not high_missing)
        out["exact_ready_inputs_ready"] = bool((not high_missing) and bill_mode and (confidence == "high"))
        if high_missing:
            if "salary_monthly_krw" in high_missing:
                out["limited_reason"] = NHIS_REASON_MISSING_SALARY_MONTHLY
            else:
                out["limited_reason"] = NHIS_REASON_MISSING_NON_SALARY_INCOME
        else:
            out["limited_reason"] = NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS
        return out

    if member_type == "regional":
        annual_income = _safe_int_or_none(prof.get("annual_income_krw"))
        non_salary_annual = _safe_int_or_none(prof.get("non_salary_annual_income_krw"))
        property_tax_base = _safe_int_or_none(prof.get("property_tax_base_total_krw"))
        high_missing: list[str] = []
        if annual_income is None:
            high_missing.append("annual_income_krw")
        if non_salary_annual is None:
            high_missing.append("non_salary_annual_income_krw")
        if property_tax_base is None:
            high_missing.append("property_tax_base_total_krw")
        out["high_confidence_missing_fields"] = high_missing
        out["exact_ready_missing_fields"] = list(high_missing)
        out["high_confidence_inputs_ready"] = bool(not high_missing)
        out["exact_ready_inputs_ready"] = bool((not high_missing) and bill_mode and (confidence == "high"))
        if high_missing:
            if "property_tax_base_total_krw" in high_missing:
                out["limited_reason"] = NHIS_REASON_MISSING_PROPERTY_TAX_BASE
            else:
                out["limited_reason"] = NHIS_REASON_MISSING_NON_SALARY_INCOME
        else:
            out["limited_reason"] = NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS
        return out

    # dependent
    out["high_confidence_missing_fields"] = []
    out["exact_ready_missing_fields"] = []
    out["high_confidence_inputs_ready"] = True
    out["exact_ready_inputs_ready"] = bool(bill_mode and (confidence == "high"))
    out["limited_reason"] = NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS
    return out


def _build_nhis_input_recovery_plan(required_inputs: dict[str, Any], reason: str) -> dict[str, list[str]]:
    high_missing = list(required_inputs.get("high_confidence_missing_fields") or [])
    exact_missing = list(required_inputs.get("exact_ready_missing_fields") or [])
    missing_fields = list(dict.fromkeys([str(v).strip() for v in [*high_missing, *exact_missing] if str(v).strip()]))

    auto_fillable_fields: list[str] = []
    low_confidence_inferable_fields: list[str] = []
    needs_user_input_fields: list[str] = []

    for field in missing_fields:
        if field == "annual_income_krw":
            auto_fillable_fields.append(field)
            continue
        if field == "non_salary_annual_income_krw":
            low_confidence_inferable_fields.append(field)
            continue
        needs_user_input_fields.append(field)

    reason_norm = str(reason or "").strip().lower()
    if reason_norm in {NHIS_REASON_MISSING_MEMBERSHIP_TYPE, NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE}:
        if "member_type" not in needs_user_input_fields:
            needs_user_input_fields.append("member_type")
    if reason_norm == NHIS_REASON_MISSING_SALARY_MONTHLY and "salary_monthly_krw" not in needs_user_input_fields:
        needs_user_input_fields.append("salary_monthly_krw")
    if reason_norm == NHIS_REASON_MISSING_PROPERTY_TAX_BASE and "property_tax_base_total_krw" not in needs_user_input_fields:
        needs_user_input_fields.append("property_tax_base_total_krw")
    if reason_norm == NHIS_REASON_MISSING_NON_SALARY_INCOME and "non_salary_annual_income_krw" not in low_confidence_inferable_fields:
        low_confidence_inferable_fields.append("non_salary_annual_income_krw")

    return {
        "auto_fillable_fields": auto_fillable_fields,
        "low_confidence_inferable_fields": low_confidence_inferable_fields,
        "needs_user_input_fields": needs_user_input_fields,
    }


def build_nhis_result_meta(
    *,
    estimate: dict[str, Any] | None,
    status: dict[str, Any] | None,
    official_ready: bool,
    profile: dict[str, Any] | None = None,
    official_data_effects: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """건보료 추정 결과를 UI에서 안전하게 안내하기 위한 상태 메타."""
    est = dict(estimate or {})
    st = dict(status or {})
    confidence = str(est.get("confidence_level") or "low")
    can_estimate = bool(est.get("can_estimate"))
    mode = str(est.get("mode") or "").strip().lower()
    update_error = str(st.get("update_error") or "").strip().lower()
    is_fallback_default = bool(st.get("is_fallback_default"))
    is_stale = bool(st.get("is_stale"))
    profile_local = dict(profile or {})
    required_inputs = evaluate_nhis_required_inputs(
        estimate=est,
        profile=profile_local,
        official_ready=bool(official_ready),
    )

    level = "normal"
    label = "공식 기준 추정"
    reason = NHIS_REASON_OK
    message = "공식 기준 일부를 반영한 추정치예요. 실제 고지액과는 차이가 있을 수 있어요."
    detail = "가입유형/소득 반영시점/재산 기준에 따라 실제 고지액과 달라질 수 있어요."

    blocked_reason = str(required_inputs.get("blocked_reason") or "")
    if blocked_reason:
        level = "blocked"
        label = "계산 제한"
        reason = blocked_reason
        message = "공식 기준 검증 전이라 숫자를 제한해서 보여주고 있어요."
        detail = "공식 기준 확인이 완료되면 다시 계산할 수 있어요."
        if reason != NHIS_REASON_MISSING_SNAPSHOT:
            message = "가입유형 입력이 부족하거나 유효하지 않아 계산을 제한하고 있어요."
            detail = "가입유형을 지역/직장/피부양자로 정확히 선택해 주세요."
        if reason == NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE:
            detail = "가입유형이 표준 값이 아니에요. 지역/직장/피부양자 중 하나를 선택해 주세요."
        elif reason == NHIS_REASON_MISSING_MEMBERSHIP_TYPE:
            detail = "가입유형을 먼저 선택해 주세요."

    if level != "blocked":
        if update_error:
            level = "limited"
            label = "제한된 추정"
            reason = NHIS_REASON_DATASET_FALLBACK
            message = "기준 데이터 갱신이 지연돼 마지막 기준으로 추정했어요."
            detail = "공단 고지액이 아니라 추정치이며, 최신 기준 반영 전까지 차이가 커질 수 있어요."
        elif is_fallback_default:
            level = "limited"
            label = "제한된 추정"
            reason = NHIS_REASON_DATASET_FALLBACK
            message = "기준 데이터 준비 중이라 기본 기준으로 추정했어요."
            detail = "기준 데이터가 준비되면 다시 계산할 수 있어요."
        elif is_stale and level == "normal":
            level = "limited"
            label = "제한된 추정"
            reason = NHIS_REASON_DATASET_FALLBACK
            message = "기준 데이터가 오래돼 보수적으로 추정했어요."
            detail = "최신 기준 반영 전까지 실제 고지액과 차이가 있을 수 있어요."
        elif level == "normal":
            if (not can_estimate) or (confidence == "low") or (not bool(required_inputs.get("high_confidence_inputs_ready"))):
                level = "limited"
                label = "제한된 추정"
                reason = str(required_inputs.get("limited_reason") or NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS)
                message = "입력 정보가 부족해 보수적으로 추정했어요. 실제보다 낮거나 다르게 보일 수 있어요."
                detail = "가입유형/소득/재산 입력을 보강하면 정확도가 올라가요."

    if level == "blocked":
        accuracy_level = "blocked"
    elif level == "limited":
        accuracy_level = "limited"
    elif bool(required_inputs.get("exact_ready_inputs_ready")) and confidence == "high" and mode.startswith("bill"):
        accuracy_level = "exact_ready"
    elif bool(required_inputs.get("high_confidence_inputs_ready")) and can_estimate and confidence in {"high", "medium"}:
        accuracy_level = "high_confidence"
    else:
        accuracy_level = "limited"

    reason = normalize_nhis_reason(
        reason,
        fallback=(NHIS_REASON_MISSING_SNAPSHOT if level == "blocked" else NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS),
    )
    recovery_plan = _build_nhis_input_recovery_plan(required_inputs, reason)
    official_effects = dict(official_data_effects or {})
    nhis_reference_date = str(official_effects.get("nhis_official_reference_date") or "").strip()
    nhis_paid_amount_krw = _safe_int_or_none(official_effects.get("nhis_official_paid_amount_krw")) or 0
    nhis_official_data_applied = bool(official_effects.get("nhis_official_data_applied"))
    nhis_recheck_recommended = bool(official_effects.get("nhis_recheck_recommended"))
    nhis_official_status_label = str(official_effects.get("nhis_official_status_label") or ("재확인 권장" if nhis_recheck_recommended else "공식 자료 없음"))

    return {
        "level": level,
        "accuracy_level": accuracy_level,
        "label": label,
        "reason": reason,
        "message": message,
        "detail": detail,
        "is_limited": level in {"limited", "blocked"},
        "confidence_level": confidence,
        "can_estimate": can_estimate,
        "official_ready": bool(official_ready),
        "required_inputs": required_inputs,
        "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
        "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
        "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
        "nhis_official_reference_date": nhis_reference_date or None,
        "nhis_official_paid_amount_krw": int(nhis_paid_amount_krw),
        "nhis_official_status_label": nhis_official_status_label,
        "nhis_official_data_applied": nhis_official_data_applied,
        "nhis_recheck_recommended": nhis_recheck_recommended,
    }


def build_nhis_recovery_cta(
    nhis_result_meta: dict[str, Any] | None,
    *,
    recovery_url: str,
) -> dict[str, Any]:
    meta = dict(nhis_result_meta or {})
    accuracy_level = str(meta.get("accuracy_level") or "limited").strip().lower()
    blocked = accuracy_level == "blocked"
    limited = accuracy_level == "limited"
    show = blocked or limited
    required_inputs = dict(meta.get("required_inputs") or {})
    missing_fields = list(
        dict.fromkeys(
            [
                *[str(v) for v in (meta.get("needs_user_input_fields") or []) if str(v).strip()],
                *[str(v) for v in (required_inputs.get("exact_ready_missing_fields") or []) if str(v).strip()],
                *[str(v) for v in (required_inputs.get("high_confidence_missing_fields") or []) if str(v).strip()],
            ]
        )
    )
    missing_labels = [NHIS_REQUIRED_FIELD_LABELS.get(field, field) for field in missing_fields]
    if blocked:
        reason = str(meta.get("reason") or "")
        if reason == NHIS_REASON_MISSING_MEMBERSHIP_TYPE:
            title = "건보 계산을 위해 가입유형 선택"
            description = "가입유형 1문항을 먼저 저장하면 다음 필수 입력 단계로 진행할 수 있어요."
            action_label = "가입유형 먼저 저장"
        else:
            title = "건보 계산에 필요한 정보 입력하기"
            description = "가입유형과 핵심 입력을 완료하면 blocked 상태를 해소할 수 있어요."
            action_label = "건보 필수 입력하기"
    elif limited:
        title = "건보 정확도 높이기"
        description = "누락 입력을 보완하면 limited 추정에서 high/exact로 올릴 수 있어요."
        action_label = "건보 입력 보완하기"
    else:
        title = ""
        description = ""
        action_label = ""
    return {
        "show": show,
        "blocked": blocked,
        "limited": limited,
        "accuracy_level": accuracy_level,
        "title": title,
        "description": description,
        "action_label": action_label,
        "url": str(recovery_url or "/dashboard/nhis"),
        "missing_fields": missing_fields,
        "missing_labels": missing_labels,
    }


def compute_nhis_monthly_buffer(
    user_pk: int,
    *,
    month_key: str | None = None,
) -> tuple[int, str | None, dict[str, Any]]:
    _legacy_profile = get_tax_profile(user_pk)
    _legacy_amount, legacy_note = get_monthly_health_insurance_buffer(_legacy_profile)
    fallback_payload = {
        "profile": {},
        "estimate": {
            "member_type": "unknown",
            "mode": "legacy",
            "confidence_level": "low",
            "health_est_krw": 0,
            "ltc_est_krw": 0,
            "total_est_krw": 0,
            "notes": [],
            "warnings": [],
            "can_estimate": False,
        },
        "snapshot": snapshot_to_display_dict(None),
        "status": {
            "is_stale": False,
            "update_error": "fallback_legacy",
            "is_fallback_default": True,
        },
    }
    fallback_payload["result_meta"] = build_nhis_result_meta(
        estimate=fallback_payload.get("estimate"),
        status=fallback_payload.get("status"),
        official_ready=False,
        profile=fallback_payload.get("profile"),
    )

    try:
        official_data_effects = collect_official_data_effects_for_user(
            db.session,
            user_pk=int(user_pk),
            month_key=month_key,
        )
        ready = check_nhis_ready()
        guard_warning = str(ready.get("guard_warning") or "").strip().lower()
        guard_warnings = [str(v).strip().lower() for v in (ready.get("guard_warnings") or []) if str(v).strip()]
        if not bool(ready.get("ready")):
            note = str(ready.get("message") or "공식 기준 업데이트가 필요해요. 잠시 후 다시 시도해 주세요.")
            payload = {
                "profile": {},
                "estimate": {
                    "member_type": "unknown",
                    "mode": "official_not_ready",
                    "confidence_level": "low",
                    "health_est_krw": 0,
                    "ltc_est_krw": 0,
                    "total_est_krw": 0,
                    "notes": [note],
                    "warnings": [],
                    "can_estimate": False,
                },
                "snapshot": snapshot_to_display_dict(None),
                "status": {
                    "is_stale": True,
                    "update_error": str(ready.get("reason") or "official_not_ready"),
                    "is_fallback_default": False,
                    "guard_warning": guard_warning,
                    "guard_warnings": guard_warnings,
                },
            }
            payload["result_meta"] = build_nhis_result_meta(
                estimate=payload.get("estimate"),
                status=payload.get("status"),
                official_ready=False,
                profile=payload.get("profile"),
                official_data_effects={
                    "nhis_official_reference_date": (
                        official_data_effects.verified_nhis_reference_date.isoformat()
                        if official_data_effects.verified_nhis_reference_date
                        else None
                    ),
                    "nhis_official_paid_amount_krw": int(official_data_effects.verified_nhis_paid_amount_krw),
                    "nhis_official_status_label": str(official_data_effects.nhis.nhis_official_status_label),
                    "nhis_official_data_applied": bool(official_data_effects.nhis.nhis_official_data_applied),
                    "nhis_recheck_recommended": bool(official_data_effects.nhis.nhis_recheck_recommended),
                },
            )
            return 0, note, payload

        status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6)
        snapshot = status.snapshot
        snapshot_display = snapshot_to_display_dict(snapshot)

        profile = load_canonical_nhis_profile(
            user_pk=user_pk,
            month_key=month_key,
            prefer_assets=False,
        )

        estimate = estimate_nhis_monthly_dict(profile, snapshot)
        amount = int(estimate.get("total_est_krw") or 0)
        note = _confidence_note(str(estimate.get("confidence_level") or "low"))
        if amount <= 0 and legacy_note:
            note = legacy_note

        if status.update_error:
            suffix = "기준 데이터 업데이트에 실패했어요. 마지막 기준으로 계산했어요."
            note = f"{note} {suffix}" if note else suffix
        elif status.is_fallback_default:
            suffix = "기준 데이터 준비 중이라 기본 기준으로 계산했어요."
            note = f"{note} {suffix}" if note else suffix

        payload = {
            "profile": profile,
            "estimate": estimate,
            "snapshot": snapshot_display,
            "status": {
                "is_stale": bool(status.is_stale),
                "update_error": status.update_error,
                "is_fallback_default": bool(status.is_fallback_default),
                "guard_warning": guard_warning,
                "guard_warnings": guard_warnings,
            },
        }
        if guard_warning == "official_snapshot_artifact_missing":
            warning_note = "공식 원문 스냅샷 파일이 없어 기준값 일치 검증으로 계산했어요."
            note = f"{note} {warning_note}" if note else warning_note
        if "snapshot_format_drift_detected" in guard_warnings:
            warning_note = "원문 포맷이 바뀌어 일부는 기준값 검증으로 계산했어요."
            note = f"{note} {warning_note}" if note else warning_note
        if "snapshot_bootstrap_default" in guard_warnings:
            warning_note = "공식 원문 수집 전 기본 기준값으로 계산했어요."
            note = f"{note} {warning_note}" if note else warning_note
        payload["result_meta"] = build_nhis_result_meta(
            estimate=estimate,
            status=payload.get("status"),
            official_ready=True,
            profile=profile,
            official_data_effects={
                "nhis_official_reference_date": (
                    official_data_effects.verified_nhis_reference_date.isoformat()
                    if official_data_effects.verified_nhis_reference_date
                    else None
                ),
                "nhis_official_paid_amount_krw": int(official_data_effects.verified_nhis_paid_amount_krw),
                "nhis_official_status_label": str(official_data_effects.nhis.nhis_official_status_label),
                "nhis_official_data_applied": bool(official_data_effects.nhis.nhis_official_data_applied),
                "nhis_recheck_recommended": bool(official_data_effects.nhis.nhis_recheck_recommended),
            },
        )
        return int(max(0, amount)), note, payload
    except Exception:
        db.session.rollback()
        note = legacy_note or "공식 기준이 준비되면 다시 계산해 드려요."
        fallback_payload["result_meta"] = build_nhis_result_meta(
            estimate=fallback_payload.get("estimate"),
            status=fallback_payload.get("status"),
            official_ready=False,
            profile=fallback_payload.get("profile"),
        )
        return 0, note, fallback_payload
