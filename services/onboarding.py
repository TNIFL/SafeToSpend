from __future__ import annotations

from typing import Any

from core.extensions import db
from core.time import utcnow
from domain.models import Settings


ONBOARDING_VERSION = "2026-03-v1"

USER_TYPE_OPTIONS = (
    {
        "value": "freelancer_33",
        "label": "프리랜서(3.3)",
        "help": "자료 추천과 기본 세금 안내를 맞추는 데 써요.",
    },
    {
        "value": "solo_business_owner",
        "label": "1인 사업자",
        "help": "사업자용 자료 추천과 정리 흐름에 반영돼요.",
    },
    {
        "value": "employee_sidejob",
        "label": "직장인 + 부업",
        "help": "본업과 부업이 섞인 안내를 나누는 데 필요해요.",
    },
    {
        "value": "unknown",
        "label": "잘 모르겠어요",
        "help": "괜찮아요. 지금은 기본 추천으로 시작할 수 있어요.",
    },
)

HEALTH_INSURANCE_OPTIONS = (
    {
        "value": "local",
        "label": "지역가입자",
        "help": "건보료 관련 안내와 추천 자료에 반영돼요.",
    },
    {
        "value": "employee",
        "label": "직장가입자",
        "help": "직장가입 기준 안내와 추천 자료에 반영돼요.",
    },
    {
        "value": "unknown",
        "label": "잘 모르겠어요",
        "help": "잘 모르셔도 괜찮아요. 나중에 바꿀 수 있어요.",
    },
)

VAT_STATUS_OPTIONS = (
    {
        "value": "vat",
        "label": "과세사업자/부가세 대상이에요",
        "help": "부가세 관련 자료 추천과 안내에 반영돼요.",
    },
    {
        "value": "non_vat",
        "label": "아니에요",
        "help": "지금 기준에서 불필요한 자료 추천을 줄이는 데 써요.",
    },
    {
        "value": "unknown",
        "label": "잘 모르겠어요",
        "help": "지금은 기본 추천으로 시작하고, 나중에 다시 고를 수 있어요.",
    },
)

_VALID_USER_TYPES = {item["value"] for item in USER_TYPE_OPTIONS}
_VALID_HEALTH_INSURANCE = {item["value"] for item in HEALTH_INSURANCE_OPTIONS}
_VALID_VAT_STATUS = {item["value"] for item in VAT_STATUS_OPTIONS}
_USER_TYPE_LABELS = {item["value"]: item["label"] for item in USER_TYPE_OPTIONS}
_HEALTH_INSURANCE_LABELS = {item["value"]: item["label"] for item in HEALTH_INSURANCE_OPTIONS}
_VAT_STATUS_LABELS = {item["value"]: item["label"] for item in VAT_STATUS_OPTIONS}


def _normalize_meta_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _first_meta_value(meta: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return value
    return ""


def _normalize_user_type(meta: dict[str, Any]) -> str:
    raw = _first_meta_value(
        meta,
        "onboarding_user_type",
        "employment_type",
        "work_type",
        "worker_type",
        "income_type",
        "occupation_type",
    )
    value = _normalize_meta_text(raw)
    if value in {"freelancer33", "freelancer", "프리랜서", "사업소득", "33", "3.3"}:
        return "freelancer_33"
    if value in {"solobusinessowner", "businessowner", "1인사업자", "개인사업자", "사업자", "개인사업"}:
        return "solo_business_owner"
    if value in {"employeesidejob", "직장인부업", "직장인+부업", "employeeandsidejob"}:
        return "employee_sidejob"
    if value in {"unknown", "잘모르겠어요", "잘모르겠음", "잘모름"}:
        return "unknown"
    return ""


def _normalize_health_insurance(meta: dict[str, Any]) -> str:
    raw = _first_meta_value(
        meta,
        "onboarding_health_insurance",
        "insurance_type",
        "nhis_type",
        "health_insurance_type",
    )
    value = _normalize_meta_text(raw)
    if value in {"local", "지역가입자", "지역"}:
        return "local"
    if value in {"employee", "직장가입자", "직장"}:
        return "employee"
    if value in {"unknown", "잘모르겠어요", "잘모르겠음", "잘모름"}:
        return "unknown"
    return ""


def _normalize_vat_status(meta: dict[str, Any]) -> str:
    raw = _first_meta_value(
        meta,
        "onboarding_vat_status",
        "vat_type",
        "business_type",
        "tax_profile",
    )
    value = _normalize_meta_text(raw)
    if value in {"vat", "vatregistered", "부가세", "부가세대상", "과세사업자"}:
        return "vat"
    if value in {"nonvat", "면세", "비과세", "아니에요", "아님"}:
        return "non_vat"
    if value in {"unknown", "잘모르겠어요", "잘모르겠음", "잘모름"}:
        return "unknown"
    if meta.get("vat_registered") is True:
        return "vat"
    if meta.get("vat_registered") is False:
        return "non_vat"
    return ""


def _ensure_settings(user_pk: int) -> Settings:
    settings = db.session.get(Settings, user_pk)
    if settings is None:
        settings = Settings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(settings)
        db.session.flush()
    return settings


def _meta(settings: Settings) -> dict[str, Any]:
    if not isinstance(settings.custom_rates, dict):
        settings.custom_rates = {}
    meta = settings.custom_rates.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    return meta


def get_onboarding_state(user_pk: int) -> dict[str, Any]:
    settings = db.session.get(Settings, user_pk)
    meta = _meta(settings) if settings is not None else {}
    user_type = _normalize_user_type(meta)
    health_insurance = _normalize_health_insurance(meta)
    vat_status = _normalize_vat_status(meta)

    return {
        "user_type": user_type,
        "health_insurance": health_insurance,
        "vat_status": vat_status,
        "completed_at": meta.get("onboarding_completed_at"),
        "skipped_at": meta.get("onboarding_skipped_at"),
        "version": str(meta.get("onboarding_version") or ""),
    }


def build_onboarding_reflection(user_pk: int) -> dict[str, Any]:
    state = get_onboarding_state(user_pk)
    user_type = state["user_type"]
    health_insurance = state["health_insurance"]
    vat_status = state["vat_status"]

    has_specific_user_type = user_type not in {"", "unknown"}
    has_specific_health_insurance = health_insurance not in {"", "unknown"}
    has_specific_vat_status = vat_status not in {"", "unknown"}

    return {
        **state,
        "user_type_label": _USER_TYPE_LABELS.get(user_type, ""),
        "health_insurance_label": _HEALTH_INSURANCE_LABELS.get(health_insurance, ""),
        "vat_status_label": _VAT_STATUS_LABELS.get(vat_status, ""),
        "has_specific_user_type": has_specific_user_type,
        "has_specific_health_insurance": has_specific_health_insurance,
        "has_specific_vat_status": has_specific_vat_status,
        "has_any_specific": any(
            (
                has_specific_user_type,
                has_specific_health_insurance,
                has_specific_vat_status,
            )
        ),
        "is_freelancer": user_type == "freelancer_33",
        "is_business_owner": user_type == "solo_business_owner",
        "is_employee_sidejob": user_type == "employee_sidejob",
        "is_local_insured": health_insurance == "local",
        "is_employee_insured": health_insurance == "employee",
        "is_vat_business": vat_status == "vat",
    }


def save_onboarding_state(
    user_pk: int,
    *,
    user_type: str,
    health_insurance: str,
    vat_status: str,
) -> None:
    if user_type not in _VALID_USER_TYPES:
        raise ValueError("invalid user_type")
    if health_insurance not in _VALID_HEALTH_INSURANCE:
        raise ValueError("invalid health_insurance")
    if vat_status not in _VALID_VAT_STATUS:
        raise ValueError("invalid vat_status")

    settings = _ensure_settings(user_pk)
    meta = _meta(settings)
    now = utcnow().isoformat()

    meta["onboarding_user_type"] = user_type
    meta["onboarding_health_insurance"] = health_insurance
    meta["onboarding_vat_status"] = vat_status
    meta["onboarding_completed_at"] = now
    meta["onboarding_skipped_at"] = None
    meta["onboarding_version"] = ONBOARDING_VERSION

    if user_type == "freelancer_33":
        meta["employment_type"] = "freelancer"
    elif user_type == "employee_sidejob":
        meta["employment_type"] = "employee_sidejob"
    elif user_type == "solo_business_owner":
        meta["employment_type"] = "business_owner"
    else:
        meta["employment_type"] = "unknown"

    if health_insurance == "local":
        meta["insurance_type"] = "local"
    elif health_insurance == "employee":
        meta["insurance_type"] = "employee"
    else:
        meta["insurance_type"] = "unknown"

    meta["vat_type"] = vat_status
    if vat_status == "vat":
        meta["vat_registered"] = True
    elif vat_status == "non_vat":
        meta["vat_registered"] = False
    else:
        meta.pop("vat_registered", None)

    custom_rates = dict(settings.custom_rates or {})
    custom_rates["_meta"] = meta
    settings.custom_rates = custom_rates
    db.session.add(settings)
    db.session.commit()


def skip_onboarding(user_pk: int) -> None:
    settings = _ensure_settings(user_pk)
    meta = _meta(settings)
    meta["onboarding_skipped_at"] = utcnow().isoformat()
    meta.setdefault("onboarding_version", ONBOARDING_VERSION)
    custom_rates = dict(settings.custom_rates or {})
    custom_rates["_meta"] = meta
    settings.custom_rates = custom_rates
    db.session.add(settings)
    db.session.commit()
