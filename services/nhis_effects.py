from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from domain.models import OfficialDataDocument

NHIS_EFFECT_STATUS_NONE = "none"
NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE = "reference_available"
NHIS_EFFECT_STATUS_STALE = "stale"
NHIS_EFFECT_STATUS_REVIEW_NEEDED = "review_needed"

NHIS_EFFECT_STRENGTH_NONE = "none"
NHIS_EFFECT_STRENGTH_WEAK = "weak"
NHIS_EFFECT_STRENGTH_MEDIUM = "medium"
NHIS_EFFECT_STRENGTH_STRONG = "strong"

SUPPORTED_NHIS_DOCUMENT_TYPES = {"nhis_payment_confirmation", "nhis_eligibility_status"}
NHIS_STALE_DAYS = 120
STRENGTH_LABELS = {
    NHIS_EFFECT_STRENGTH_NONE: "없음",
    NHIS_EFFECT_STRENGTH_WEAK: "약",
    NHIS_EFFECT_STRENGTH_MEDIUM: "보통",
    NHIS_EFFECT_STRENGTH_STRONG: "강",
}

NHIS_VISUAL_FEEDBACK_LEVEL_NONE = "none"
NHIS_VISUAL_FEEDBACK_LEVEL_SOFT = "soft"
NHIS_VISUAL_FEEDBACK_LEVEL_MEDIUM = "medium"
NHIS_VISUAL_FEEDBACK_LEVEL_REVIEW = "review"

VERIFICATION_LEVEL_NONE = "none"
VERIFICATION_LEVEL_LOW = "low"
VERIFICATION_LEVEL_MEDIUM = "medium"
VERIFICATION_LEVEL_HIGH = "high"
VERIFICATION_LEVEL_REVIEW = "review"

CONFIDENCE_LABELS = {
    VERIFICATION_LEVEL_NONE: "없음",
    VERIFICATION_LEVEL_LOW: "참고용",
    VERIFICATION_LEVEL_MEDIUM: "참고 신뢰도 보통",
    VERIFICATION_LEVEL_HIGH: "참고 신뢰도 높음",
    VERIFICATION_LEVEL_REVIEW: "재확인 필요",
}

VERIFICATION_BADGES = {
    VERIFICATION_LEVEL_NONE: "검증 정보 없음",
    VERIFICATION_LEVEL_LOW: "참고 자료",
    VERIFICATION_LEVEL_MEDIUM: "구조 검증 통과",
    VERIFICATION_LEVEL_HIGH: "기관 확인 메타 있음",
    VERIFICATION_LEVEL_REVIEW: "검토 또는 재확인 필요",
}


def _serialize_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _payload_int(document: Any, key: str) -> int:
    payload = dict(getattr(document, "extracted_payload_json", {}) or {})
    value = payload.get(key)
    if value in (None, ""):
        return 0
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _payload_text(document: Any, key: str) -> str:
    payload = dict(getattr(document, "extracted_payload_json", {}) or {})
    return str(payload.get(key) or "").strip()


def _payload_date(document: Any, key: str) -> date | None:
    payload = dict(getattr(document, "extracted_payload_json", {}) or {})
    value = payload.get(key)
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def _reference_date(document: Any) -> date | None:
    value = getattr(document, "verified_reference_date", None)
    return value if isinstance(value, date) else None


def _nhis_document_kind_labels(document_types: Iterable[str]) -> tuple[str, ...]:
    labels: list[str] = []
    doc_type_set = {str(value or "") for value in document_types}
    if "nhis_payment_confirmation" in doc_type_set:
        labels.append("납부확인 참고")
    if "nhis_eligibility_status" in doc_type_set:
        labels.append("자격자료 참고")
    return tuple(labels)


def _doc_verification_status(document: Any) -> str:
    return str(getattr(document, "verification_status", "") or "").strip().lower()


def _doc_verification_source(document: Any) -> str:
    return str(getattr(document, "verification_source", "") or "").strip()


def _is_verified_document(document: Any) -> bool:
    if _doc_verification_status(document) == "succeeded" and _doc_verification_source(document):
        return True
    return str(getattr(document, "trust_grade", "") or "").strip() == "A"


def _build_nhis_verification_summary(status: str, documents: Iterable[Any]) -> dict[str, Any]:
    docs = tuple(doc for doc in documents if doc is not None)
    if status == NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE:
        if any(_is_verified_document(doc) for doc in docs):
            return {
                "nhis_verification_level": VERIFICATION_LEVEL_HIGH,
                "nhis_confidence_label": CONFIDENCE_LABELS[VERIFICATION_LEVEL_HIGH],
                "nhis_verification_badge": VERIFICATION_BADGES[VERIFICATION_LEVEL_HIGH],
                "nhis_verification_hint": "기관 확인 메타가 있어도 NHIS는 참고 상태로만 연결해요.",
                "nhis_is_high_confidence": True,
            }
        return {
            "nhis_verification_level": VERIFICATION_LEVEL_MEDIUM,
            "nhis_confidence_label": CONFIDENCE_LABELS[VERIFICATION_LEVEL_MEDIUM],
            "nhis_verification_badge": VERIFICATION_BADGES[VERIFICATION_LEVEL_MEDIUM],
            "nhis_verification_hint": "구조 검증 자료 기준으로 참고 상태만 보여 줘요.",
            "nhis_is_high_confidence": False,
        }
    if status == NHIS_EFFECT_STATUS_STALE:
        return {
            "nhis_verification_level": VERIFICATION_LEVEL_REVIEW,
            "nhis_confidence_label": CONFIDENCE_LABELS[VERIFICATION_LEVEL_REVIEW],
            "nhis_verification_badge": "기준일 지난 자료",
            "nhis_verification_hint": "기준일이 지나 최신 상태를 다시 확인하는 편이 안전해요.",
            "nhis_is_high_confidence": False,
        }
    if status == NHIS_EFFECT_STATUS_REVIEW_NEEDED:
        return {
            "nhis_verification_level": VERIFICATION_LEVEL_REVIEW,
            "nhis_confidence_label": CONFIDENCE_LABELS[VERIFICATION_LEVEL_REVIEW],
            "nhis_verification_badge": "검토 필요",
            "nhis_verification_hint": "검토가 더 필요한 자료라 강한 신뢰 표현을 쓰지 않아요.",
            "nhis_is_high_confidence": False,
        }
    return {
        "nhis_verification_level": VERIFICATION_LEVEL_NONE,
        "nhis_confidence_label": CONFIDENCE_LABELS[VERIFICATION_LEVEL_NONE],
        "nhis_verification_badge": VERIFICATION_BADGES[VERIFICATION_LEVEL_NONE],
        "nhis_verification_hint": "이번 달 참고로 보여 줄 검증 자료가 없어요.",
        "nhis_is_high_confidence": False,
    }


def _nhis_feedback_level(status: str, strength: str, verification_level: str) -> str:
    if status == NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE:
        if verification_level == VERIFICATION_LEVEL_HIGH:
            return NHIS_VISUAL_FEEDBACK_LEVEL_MEDIUM
        return NHIS_VISUAL_FEEDBACK_LEVEL_MEDIUM if strength in {
            NHIS_EFFECT_STRENGTH_MEDIUM,
            NHIS_EFFECT_STRENGTH_STRONG,
        } else NHIS_VISUAL_FEEDBACK_LEVEL_SOFT
    if status in {NHIS_EFFECT_STATUS_STALE, NHIS_EFFECT_STATUS_REVIEW_NEEDED}:
        return NHIS_VISUAL_FEEDBACK_LEVEL_REVIEW
    return NHIS_VISUAL_FEEDBACK_LEVEL_NONE


def is_nhis_snapshot_stale(reference_date: date | None, *, today: date | None = None) -> bool:
    if not reference_date:
        return True
    current_day = today or date.today()
    return (current_day - reference_date) > timedelta(days=NHIS_STALE_DAYS)


def _sort_key(document: Any) -> tuple[int, date, int]:
    grade = str(getattr(document, "trust_grade", "") or "")
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}.get(grade, 0)
    reference_date = _reference_date(document) or date.min
    doc_id = int(getattr(document, "id", 0) or 0)
    return (grade_rank, reference_date, doc_id)


def _eligibility_reason_fragment(document: Any) -> str:
    subscriber_type = _payload_text(document, "subscriber_type")
    eligibility_status = _payload_text(document, "eligibility_status")
    latest_change = _payload_date(document, "latest_status_change_date")
    fragments: list[str] = []
    if subscriber_type or eligibility_status:
        if subscriber_type and eligibility_status:
            fragments.append(f"{subscriber_type} {eligibility_status} 기준")
        else:
            fragments.append(subscriber_type or eligibility_status)
    if latest_change:
        fragments.append(f"최근 변동일 {latest_change.isoformat()}")
    return " · ".join(fragment for fragment in fragments if fragment)


def _eligibility_recheck_required(document: Any, *, today: date) -> bool:
    eligibility_status = _payload_text(document, "eligibility_status")
    eligibility_end = _payload_date(document, "eligibility_end_date")
    latest_change = _payload_date(document, "latest_status_change_date")
    if eligibility_end is not None:
        return True
    if latest_change and (today - latest_change) <= timedelta(days=60):
        return True
    return any(token in eligibility_status for token in ("상실", "변동", "종료"))


def build_nhis_effect_state(documents: Iterable[Any], *, today: date | None = None) -> dict[str, Any]:
    current_day = today or date.today()
    payment_candidates = []
    eligibility_candidates = []
    review_candidates = []
    for document in documents:
        document_type = str(getattr(document, "document_type", "") or "")
        if document_type not in SUPPORTED_NHIS_DOCUMENT_TYPES:
            continue
        if str(getattr(document, "parse_status", "") or "") != "parsed":
            review_candidates.append(document)
            continue
        if str(getattr(document, "structure_validation_status", "") or "") != "passed":
            review_candidates.append(document)
            continue
        if str(getattr(document, "trust_grade", "") or "") == "D":
            review_candidates.append(document)
            continue
        if document_type == "nhis_payment_confirmation":
            payment_candidates.append(document)
        elif document_type == "nhis_eligibility_status":
            eligibility_candidates.append(document)

    payment_candidates = sorted(payment_candidates, key=_sort_key, reverse=True)
    eligibility_candidates = sorted(eligibility_candidates, key=_sort_key, reverse=True)
    review_candidates = sorted(review_candidates, key=_sort_key, reverse=True)
    best_payment = payment_candidates[0] if payment_candidates else None
    best_eligibility = eligibility_candidates[0] if eligibility_candidates else None
    best_documents = [doc for doc in (best_payment, best_eligibility) if doc is not None]

    state: dict[str, Any] = {
        "nhis_latest_paid_amount_krw": 0,
        "nhis_reference_date": None,
        "nhis_effect_strength": NHIS_EFFECT_STRENGTH_NONE,
        "nhis_effect_status": NHIS_EFFECT_STATUS_NONE,
        "nhis_effect_reason": "최근 공식 납부 기준 참고 자료가 없어요.",
        "nhis_recheck_required": False,
        "nhis_effect_source_count": 0,
        "nhis_effect_documents": tuple(),
        "nhis_effect_document_types": tuple(),
        **_build_nhis_verification_summary(NHIS_EFFECT_STATUS_NONE, ()),
    }

    if not best_documents and review_candidates:
        state.update(
            {
                "nhis_effect_status": NHIS_EFFECT_STATUS_REVIEW_NEEDED,
                "nhis_effect_reason": "검토가 더 필요한 자료라 건보료 참고 상태를 보류했어요.",
                "nhis_recheck_required": True,
                "nhis_effect_source_count": len(review_candidates),
                "nhis_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in review_candidates),
                "nhis_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in review_candidates
                ),
                **_build_nhis_verification_summary(NHIS_EFFECT_STATUS_REVIEW_NEEDED, review_candidates),
            }
        )
        return state

    if not best_documents:
        return state

    reference_candidates = [_reference_date(doc) for doc in best_documents if _reference_date(doc)]
    reference_date = max(reference_candidates) if reference_candidates else None
    latest_paid_amount = _payload_int(best_payment, "total_paid_amount_krw") if best_payment else 0
    stale = is_nhis_snapshot_stale(reference_date, today=current_day)
    grade = max(
        (str(getattr(doc, "trust_grade", "") or "") for doc in best_documents),
        key=lambda value: {"A": 4, "B": 3, "C": 2, "D": 1}.get(value, 0),
        default="C",
    )
    eligibility_fragment = _eligibility_reason_fragment(best_eligibility) if best_eligibility else ""
    recheck_required = False
    if best_eligibility:
        recheck_required = _eligibility_recheck_required(best_eligibility, today=current_day)
    if stale:
        reason = "기준일이 조금 지난 공식 NHIS 자료라 참고로만 보고 다시 확인하는 편이 안전해요."
        if eligibility_fragment:
            reason = f"{reason} 자격 상태 참고: {eligibility_fragment}."
        state.update(
            {
                "nhis_latest_paid_amount_krw": latest_paid_amount,
                "nhis_reference_date": _serialize_date(reference_date),
                "nhis_effect_strength": NHIS_EFFECT_STRENGTH_WEAK,
                "nhis_effect_status": NHIS_EFFECT_STATUS_STALE,
                "nhis_effect_reason": reason,
                "nhis_recheck_required": True,
                "nhis_effect_source_count": len(payment_candidates) + len(eligibility_candidates),
                "nhis_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in (*payment_candidates, *eligibility_candidates)),
                "nhis_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in (*payment_candidates, *eligibility_candidates)
                ),
                **_build_nhis_verification_summary(NHIS_EFFECT_STATUS_STALE, best_documents),
            }
        )
        return state

    strength = NHIS_EFFECT_STRENGTH_WEAK
    if grade == "A":
        strength = NHIS_EFFECT_STRENGTH_STRONG
    elif grade == "B":
        strength = NHIS_EFFECT_STRENGTH_MEDIUM

    reason = "최근 공식 납부 기준 참고 상태로만 연결하고, 건보료 계산값을 바로 덮어쓰지는 않아요."
    if best_payment and best_eligibility and eligibility_fragment:
        reason = f"{reason} 자격 상태 참고: {eligibility_fragment}."
    elif best_eligibility and eligibility_fragment:
        reason = f"최근 공식 자격 상태 자료를 참고하고 있어요. {eligibility_fragment}."

    state.update(
        {
            "nhis_latest_paid_amount_krw": latest_paid_amount,
            "nhis_reference_date": _serialize_date(reference_date),
            "nhis_effect_strength": strength,
            "nhis_effect_status": NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE,
            "nhis_effect_reason": reason,
            "nhis_recheck_required": recheck_required,
            "nhis_effect_source_count": len(payment_candidates) + len(eligibility_candidates),
            "nhis_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in (*payment_candidates, *eligibility_candidates)),
            "nhis_effect_document_types": tuple(
                str(getattr(doc, "document_type", "") or "") for doc in (*payment_candidates, *eligibility_candidates)
            ),
            **_build_nhis_verification_summary(NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE, best_documents),
        }
    )
    return state


def collect_nhis_effects_for_user(user_pk: int, *, today: date | None = None) -> dict[str, Any]:
    documents = (
        OfficialDataDocument.query.filter_by(user_pk=int(user_pk), source_system="nhis")
        .filter(OfficialDataDocument.document_type.in_(sorted(SUPPORTED_NHIS_DOCUMENT_TYPES)))
        .order_by(OfficialDataDocument.verified_reference_date.desc(), OfficialDataDocument.id.desc())
        .all()
    )
    return build_nhis_effect_state(documents, today=today)


def build_nhis_effect_notice_context(effect_state: dict[str, Any] | None) -> dict[str, Any]:
    effect = dict(effect_state or {})
    status = str(effect.get("nhis_effect_status") or NHIS_EFFECT_STATUS_NONE)
    document_types = tuple(effect.get("nhis_effect_document_types") or ())
    document_kind_labels = _nhis_document_kind_labels(document_types)
    return {
        "show": status != NHIS_EFFECT_STATUS_NONE,
        "status": status,
        "title": "NHIS 참고 상태",
        "summary": str(effect.get("nhis_effect_reason") or ""),
        "strength": str(effect.get("nhis_effect_strength") or NHIS_EFFECT_STRENGTH_NONE),
        "strength_label": STRENGTH_LABELS.get(
            str(effect.get("nhis_effect_strength") or NHIS_EFFECT_STRENGTH_NONE),
            "없음",
        ),
        "reference_date": effect.get("nhis_reference_date"),
        "latest_paid_amount_krw": int(effect.get("nhis_latest_paid_amount_krw") or 0),
        "source_count": int(effect.get("nhis_effect_source_count") or 0),
        "document_kind_summary": ", ".join(document_kind_labels),
        "recheck_required": bool(effect.get("nhis_recheck_required")),
        "confidence_label": str(effect.get("nhis_confidence_label") or CONFIDENCE_LABELS[VERIFICATION_LEVEL_NONE]),
        "verification_badge": str(effect.get("nhis_verification_badge") or VERIFICATION_BADGES[VERIFICATION_LEVEL_NONE]),
        "verification_hint": str(effect.get("nhis_verification_hint") or ""),
        "verification_level": str(effect.get("nhis_verification_level") or VERIFICATION_LEVEL_NONE),
        "is_high_confidence_effect": bool(effect.get("nhis_is_high_confidence")),
    }


def build_nhis_visual_feedback(effect_state: dict[str, Any] | None) -> dict[str, Any]:
    effect = dict(effect_state or {})
    status = str(effect.get("nhis_effect_status") or NHIS_EFFECT_STATUS_NONE)
    strength = str(effect.get("nhis_effect_strength") or NHIS_EFFECT_STRENGTH_NONE)
    document_types = tuple(effect.get("nhis_effect_document_types") or ())
    source_labels = _nhis_document_kind_labels(document_types)
    verification_level = str(effect.get("nhis_verification_level") or VERIFICATION_LEVEL_NONE)
    return {
        "show": status != NHIS_EFFECT_STATUS_NONE,
        "nhis_effect_status": status,
        "nhis_reference_date": effect.get("nhis_reference_date"),
        "nhis_latest_paid_amount_krw": int(effect.get("nhis_latest_paid_amount_krw") or 0),
        "nhis_feedback_level": _nhis_feedback_level(status, strength, verification_level),
        "nhis_feedback_reason": str(effect.get("nhis_effect_reason") or ""),
        "should_highlight_reference": status in {
            NHIS_EFFECT_STATUS_REFERENCE_AVAILABLE,
            NHIS_EFFECT_STATUS_STALE,
        },
        "should_animate": False,
        "source_labels": source_labels,
        "source_count": int(effect.get("nhis_effect_source_count") or 0),
        "recheck_required": bool(effect.get("nhis_recheck_required")),
        "strength": strength,
        "strength_label": STRENGTH_LABELS.get(strength, "없음"),
        "document_kind_summary": ", ".join(source_labels),
        "confidence_label": str(effect.get("nhis_confidence_label") or CONFIDENCE_LABELS[VERIFICATION_LEVEL_NONE]),
        "verification_badge": str(effect.get("nhis_verification_badge") or VERIFICATION_BADGES[VERIFICATION_LEVEL_NONE]),
        "verification_hint": str(effect.get("nhis_verification_hint") or ""),
        "verification_level": verification_level,
        "is_high_confidence_effect": bool(effect.get("nhis_is_high_confidence")),
    }
