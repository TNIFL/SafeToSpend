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
    user_type = str(meta.get("onboarding_user_type") or "")
    if not user_type:
        employment_type = str(meta.get("employment_type") or "")
        if employment_type == "freelancer":
            user_type = "freelancer_33"
        elif employment_type == "business_owner":
            user_type = "solo_business_owner"
        elif employment_type == "employee_sidejob":
            user_type = "employee_sidejob"

    health_insurance = str(meta.get("onboarding_health_insurance") or "")
    if not health_insurance:
        insurance_type = str(meta.get("insurance_type") or "")
        if insurance_type in {"local", "employee"}:
            health_insurance = insurance_type

    vat_status = str(meta.get("onboarding_vat_status") or "")
    if not vat_status:
        vat_type = str(meta.get("vat_type") or "")
        if vat_type in {"vat", "non_vat", "unknown"}:
            vat_status = vat_type
        elif meta.get("vat_registered") is True:
            vat_status = "vat"
        elif meta.get("vat_registered") is False:
            vat_status = "non_vat"

    return {
        "user_type": user_type,
        "health_insurance": health_insurance,
        "vat_status": vat_status,
        "completed_at": meta.get("onboarding_completed_at"),
        "skipped_at": meta.get("onboarding_skipped_at"),
        "version": str(meta.get("onboarding_version") or ""),
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
