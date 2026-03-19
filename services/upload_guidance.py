from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.extensions import db
from domain.models import SafeToSpendSettings


@dataclass(frozen=True)
class UploadGuidanceProfile:
    is_local_insured: bool
    is_employee_insured: bool
    is_freelancer: bool
    is_vat_business: bool


def _normalize_meta_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _meta_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_meta_text(value) in {"1", "true", "yes", "y", "on", "예", "네", "맞음", "registered"}


def _user_profile_meta(user_pk: int) -> dict[str, Any]:
    settings = db.session.get(SafeToSpendSettings, user_pk)
    if not settings or not isinstance(settings.custom_rates, dict):
        return {}
    meta = settings.custom_rates.get("_meta")
    return meta if isinstance(meta, dict) else {}


def build_upload_guidance_profile(user_pk: int) -> UploadGuidanceProfile:
    meta = _user_profile_meta(user_pk)

    insurance_value = _normalize_meta_text(
        meta.get("insurance_type")
        or meta.get("nhis_type")
        or meta.get("health_insurance_type")
    )
    work_value = _normalize_meta_text(
        meta.get("employment_type")
        or meta.get("work_type")
        or meta.get("worker_type")
        or meta.get("income_type")
        or meta.get("occupation_type")
    )
    business_value = _normalize_meta_text(
        meta.get("business_type")
        or meta.get("tax_profile")
        or meta.get("vat_type")
    )

    return UploadGuidanceProfile(
        is_local_insured=insurance_value in {"local", "지역가입자", "지역"},
        is_employee_insured=insurance_value in {"employee", "직장가입자", "직장"},
        is_freelancer=work_value in {
            "freelancer",
            "프리랜서",
            "selfemployed",
            "self-employed",
            "사업소득",
            "3.3",
        },
        is_vat_business=_meta_truthy(meta.get("vat_registered")) or business_value in {
            "vat",
            "vatregistered",
            "부가세",
            "부가세대상",
            "과세사업자",
        },
    )


def doc_item(*, title: str, formats: str, reason: str) -> dict[str, str]:
    return {"title": title, "formats": formats, "reason": reason}


def build_recommendation_guidance(
    *,
    user_pk: int,
    recommendation_title: str,
    empty_recommendation_hint: str,
    profile_recommendations: Mapping[str, Mapping[str, Any]],
    additional_documents: Sequence[Mapping[str, str]],
    baseline_documents: Sequence[Mapping[str, str]],
    extra_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = build_upload_guidance_profile(user_pk)
    recommendation_notes: list[str] = []
    recommended_documents: list[dict[str, str]] = []

    for key, active in (
        ("is_local_insured", profile.is_local_insured),
        ("is_employee_insured", profile.is_employee_insured),
        ("is_freelancer", profile.is_freelancer),
        ("is_vat_business", profile.is_vat_business),
    ):
        if not active:
            continue
        branch = profile_recommendations.get(key)
        if not branch:
            continue
        note = str(branch.get("note") or "").strip()
        if note:
            recommendation_notes.append(note)
        for item in branch.get("documents", ()):
            recommended_documents.append(dict(item))

    deduped_recommended: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for item in recommended_documents:
        title = item.get("title", "")
        if title in seen_titles:
            continue
        deduped_recommended.append(item)
        if title:
            seen_titles.add(title)

    filtered_additional = [dict(item) for item in additional_documents if item.get("title") not in seen_titles]
    baseline_items = [dict(item) for item in baseline_documents]

    context = {
        "guidance_recommendation_title": recommendation_title,
        "guidance_recommendation_hint": (
            " ".join(recommendation_notes) if recommendation_notes else empty_recommendation_hint
        ),
        "guidance_recommended_documents": deduped_recommended,
        "guidance_additional_documents": filtered_additional,
        "guidance_baseline_documents": baseline_items,
    }
    if extra_context:
        context.update(extra_context)
    return context
