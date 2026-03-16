from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from domain.models import OfficialDataDocument

TAX_EFFECT_STATUS_NONE = "none"
TAX_EFFECT_STATUS_APPLIED = "applied"
TAX_EFFECT_STATUS_REFERENCE_ONLY = "reference_only"
TAX_EFFECT_STATUS_STALE = "stale"
TAX_EFFECT_STATUS_REVIEW_NEEDED = "review_needed"

TAX_EFFECT_STRENGTH_NONE = "none"
TAX_EFFECT_STRENGTH_WEAK = "weak"
TAX_EFFECT_STRENGTH_MEDIUM = "medium"
TAX_EFFECT_STRENGTH_STRONG = "strong"

WITHHOLDING_TAX_DOCUMENT_TYPES = {"hometax_withholding_statement"}
PAID_TAX_DOCUMENT_TYPES = {"hometax_tax_payment_history"}
SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES = WITHHOLDING_TAX_DOCUMENT_TYPES | PAID_TAX_DOCUMENT_TYPES
SUPPORTED_REFERENCE_TAX_DOCUMENT_TYPES = {"hometax_business_card_usage"}
STALE_DAYS_DEFAULT = 180
STRENGTH_LABELS = {
    TAX_EFFECT_STRENGTH_NONE: "없음",
    TAX_EFFECT_STRENGTH_WEAK: "약",
    TAX_EFFECT_STRENGTH_MEDIUM: "보통",
    TAX_EFFECT_STRENGTH_STRONG: "강",
}

VISUAL_FEEDBACK_LEVEL_NONE = "none"
VISUAL_FEEDBACK_LEVEL_SOFT = "soft"
VISUAL_FEEDBACK_LEVEL_MEDIUM = "medium"
VISUAL_FEEDBACK_LEVEL_STRONG = "strong"
VISUAL_FEEDBACK_LEVEL_REVIEW = "review"


def _month_bounds(month_key: str) -> tuple[date, date]:
    year, month = (month_key or "").split("-", 1)
    y = int(year)
    m = int(month)
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start, end


def _serialize_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _payload_int(document: Any, *keys: str) -> int:
    payload = dict(getattr(document, "extracted_payload_json", {}) or {})
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except Exception:
            continue
    return 0


def _doc_reference_date(document: Any) -> date | None:
    value = getattr(document, "verified_reference_date", None)
    return value if isinstance(value, date) else None


def _doc_period(document: Any) -> tuple[date | None, date | None]:
    start = getattr(document, "document_period_start", None)
    end = getattr(document, "document_period_end", None)
    return (start if isinstance(start, date) else None, end if isinstance(end, date) else None)


def _tax_document_kind_labels(
    document_types: Iterable[str],
    *,
    status: str,
    supported_reference_count: int = 0,
) -> tuple[str, ...]:
    labels: list[str] = []
    doc_type_set = {str(value or "") for value in document_types}
    if "hometax_withholding_statement" in doc_type_set:
        labels.append("원천징수 반영" if status == TAX_EFFECT_STATUS_APPLIED else "원천징수 참고")
    if "hometax_tax_payment_history" in doc_type_set:
        labels.append("납부내역 반영" if status == TAX_EFFECT_STATUS_APPLIED else "납부내역 참고")
    if int(supported_reference_count or 0) > 0:
        labels.append("사업용 카드 참고")
    return tuple(labels)


def _tax_feedback_level(status: str, strength: str, *, has_delta: bool) -> str:
    if status == TAX_EFFECT_STATUS_APPLIED and has_delta:
        return VISUAL_FEEDBACK_LEVEL_STRONG if strength in {
            TAX_EFFECT_STRENGTH_MEDIUM,
            TAX_EFFECT_STRENGTH_STRONG,
        } else VISUAL_FEEDBACK_LEVEL_MEDIUM
    if status == TAX_EFFECT_STATUS_REFERENCE_ONLY:
        return VISUAL_FEEDBACK_LEVEL_SOFT
    if status in {TAX_EFFECT_STATUS_STALE, TAX_EFFECT_STATUS_REVIEW_NEEDED}:
        return VISUAL_FEEDBACK_LEVEL_REVIEW
    return VISUAL_FEEDBACK_LEVEL_NONE


def _month_relevant(document: Any, month_key: str) -> bool:
    month_start, month_end = _month_bounds(month_key)
    period_start, period_end = _doc_period(document)
    if period_start and period_end:
        return period_start < month_end and period_end >= month_start
    reference_date = _doc_reference_date(document)
    if reference_date:
        return month_start <= reference_date < month_end
    return False


def _sort_key(document: Any) -> tuple[int, date, int]:
    grade = str(getattr(document, "trust_grade", "") or "").strip()
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}.get(grade, 0)
    reference_date = _doc_reference_date(document) or date.min
    doc_id = int(getattr(document, "id", 0) or 0)
    return (grade_rank, reference_date, doc_id)


def _is_stale(document: Any, *, today: date | None = None, stale_days: int = STALE_DAYS_DEFAULT) -> bool:
    reference_date = _doc_reference_date(document)
    if not reference_date:
        return True
    current_day = today or date.today()
    return (current_day - reference_date) > timedelta(days=stale_days)


def _is_direct_tax_candidate(document: Any) -> bool:
    return (
        str(getattr(document, "document_type", "") or "") in SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES
        and str(getattr(document, "parse_status", "") or "") == "parsed"
        and str(getattr(document, "structure_validation_status", "") or "") == "passed"
        and str(getattr(document, "trust_grade", "") or "") in {"A", "B"}
    )


def _is_reference_tax_candidate(document: Any) -> bool:
    return (
        str(getattr(document, "document_type", "") or "") in SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES
        and str(getattr(document, "parse_status", "") or "") == "parsed"
        and str(getattr(document, "structure_validation_status", "") or "") == "passed"
        and str(getattr(document, "trust_grade", "") or "") == "C"
    )


def _is_tax_review_candidate(document: Any) -> bool:
    return (
        str(getattr(document, "document_type", "") or "") in SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES
        and (
            str(getattr(document, "parse_status", "") or "") != "parsed"
            or str(getattr(document, "trust_grade", "") or "") == "D"
            or str(getattr(document, "structure_validation_status", "") or "") != "passed"
        )
    )


def select_best_official_tax_documents(
    documents: Iterable[Any],
    *,
    month_key: str,
    today: date | None = None,
) -> dict[str, list[Any]]:
    selected = {
        "applied": [],
        "reference_only": [],
        "stale": [],
        "review_needed": [],
        "ignored": [],
        "supported_reference": [],
    }
    for document in documents:
        document_type = str(getattr(document, "document_type", "") or "")
        if document_type not in (SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES | SUPPORTED_REFERENCE_TAX_DOCUMENT_TYPES):
            selected["ignored"].append(document)
            continue
        if not _month_relevant(document, month_key):
            selected["ignored"].append(document)
            continue
        if document_type in SUPPORTED_REFERENCE_TAX_DOCUMENT_TYPES:
            selected["supported_reference"].append(document)
            continue
        if _is_stale(document, today=today):
            selected["stale"].append(document)
        elif _is_direct_tax_candidate(document):
            selected["applied"].append(document)
        elif _is_reference_tax_candidate(document):
            selected["reference_only"].append(document)
        elif _is_tax_review_candidate(document):
            selected["review_needed"].append(document)
        else:
            selected["ignored"].append(document)

    for key in ("applied", "reference_only", "stale", "review_needed", "supported_reference"):
        selected[key] = sorted(selected[key], key=_sort_key, reverse=True)
    return selected


def build_official_tax_effect_state(
    documents: Iterable[Any],
    *,
    month_key: str,
    today: date | None = None,
) -> dict[str, Any]:
    buckets = select_best_official_tax_documents(documents, month_key=month_key, today=today)
    best_applied = buckets["applied"][0] if buckets["applied"] else None
    best_reference = buckets["reference_only"][0] if buckets["reference_only"] else None
    best_stale = buckets["stale"][0] if buckets["stale"] else None
    best_review = buckets["review_needed"][0] if buckets["review_needed"] else None
    applied_docs = buckets["applied"]
    reference_docs = buckets["reference_only"]
    best_withholding = next((doc for doc in applied_docs if str(getattr(doc, "document_type", "") or "") in WITHHOLDING_TAX_DOCUMENT_TYPES), None)
    best_paid_tax = next((doc for doc in applied_docs if str(getattr(doc, "document_type", "") or "") in PAID_TAX_DOCUMENT_TYPES), None)
    best_reference_withholding = next((doc for doc in reference_docs if str(getattr(doc, "document_type", "") or "") in WITHHOLDING_TAX_DOCUMENT_TYPES), None)
    best_reference_paid_tax = next((doc for doc in reference_docs if str(getattr(doc, "document_type", "") or "") in PAID_TAX_DOCUMENT_TYPES), None)

    state: dict[str, Any] = {
        "official_withheld_tax_krw": 0,
        "official_paid_tax_krw": 0,
        "official_tax_reference_date": None,
        "official_tax_effect_strength": TAX_EFFECT_STRENGTH_NONE,
        "official_tax_effect_source_count": 0,
        "official_tax_effect_status": TAX_EFFECT_STATUS_NONE,
        "official_tax_effect_reason": "이번 달에 자동 반영 가능한 홈택스 공식 자료가 없어요.",
        "official_tax_effect_documents": tuple(),
        "official_tax_effect_document_types": tuple(),
        "official_tax_effect_supported_reference_count": len(buckets["supported_reference"]),
    }

    if best_applied:
        withheld = _payload_int(best_withholding, "total_withheld_tax_krw") if best_withholding else 0
        paid = _payload_int(best_paid_tax, "paid_tax_total_krw", "total_paid_tax_krw", "total_paid_amount_krw") if best_paid_tax else 0
        grade = max(
            (str(getattr(doc, "trust_grade", "") or "") for doc in applied_docs),
            key=lambda value: {"A": 4, "B": 3, "C": 2, "D": 1}.get(value, 0),
            default="B",
        )
        reference_date_candidates = [_doc_reference_date(doc) for doc in applied_docs if _doc_reference_date(doc)]
        reference_date = max(reference_date_candidates) if reference_date_candidates else _doc_reference_date(best_applied)
        applied_parts = []
        if best_withholding:
            applied_parts.append("이미 빠진 세금")
        if best_paid_tax:
            applied_parts.append("이미 납부한 세금")
        effect_reason = "공식 자료 기준으로 세금 값을 반영했어요."
        if applied_parts:
            joined = "과 ".join(applied_parts) if len(applied_parts) == 2 else applied_parts[0]
            effect_reason = f"공식 자료 기준으로 {joined}을 반영했어요."
        if grade == "A":
            effect_reason = effect_reason.replace("공식 자료 기준으로", "기관 확인 메타가 있는 공식 자료 기준으로")
        elif not best_paid_tax or not best_withholding:
            effect_reason = effect_reason.replace("공식 자료 기준으로", "공식 양식 구조를 검증한 자료 기준으로")
        state.update(
            {
                "official_withheld_tax_krw": withheld,
                "official_paid_tax_krw": paid,
                "official_tax_reference_date": _serialize_date(reference_date),
                "official_tax_effect_strength": TAX_EFFECT_STRENGTH_STRONG if grade == "A" else TAX_EFFECT_STRENGTH_MEDIUM,
                "official_tax_effect_source_count": len(applied_docs),
                "official_tax_effect_status": TAX_EFFECT_STATUS_APPLIED,
                "official_tax_effect_reason": effect_reason,
                "official_tax_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in applied_docs),
                "official_tax_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in applied_docs
                ),
            }
        )
        return state

    if best_reference:
        reference_date_candidates = [_doc_reference_date(doc) for doc in reference_docs if _doc_reference_date(doc)]
        reference_date = max(reference_date_candidates) if reference_date_candidates else _doc_reference_date(best_reference)
        if best_reference_paid_tax:
            reason = "업로드한 납부 자료 기준이라 이번 단계에서는 직접 차감하지 않고 참고 상태로만 유지해요."
        elif best_reference_withholding:
            reason = "업로드한 자료 기준이라 이번 단계에서는 직접 차감하지 않고 참고 상태로만 유지해요."
        else:
            reason = "업로드한 자료 기준이라 이번 단계에서는 직접 차감하지 않고 참고 상태로만 유지해요."
        state.update(
            {
                "official_tax_reference_date": _serialize_date(reference_date),
                "official_tax_effect_strength": TAX_EFFECT_STRENGTH_WEAK,
                "official_tax_effect_source_count": len(reference_docs),
                "official_tax_effect_status": TAX_EFFECT_STATUS_REFERENCE_ONLY,
                "official_tax_effect_reason": reason,
                "official_tax_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in reference_docs),
                "official_tax_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in reference_docs
                ),
            }
        )
        return state

    if best_stale:
        state.update(
            {
                "official_tax_reference_date": _serialize_date(_doc_reference_date(best_stale)),
                "official_tax_effect_strength": TAX_EFFECT_STRENGTH_WEAK,
                "official_tax_effect_source_count": len(buckets["stale"]),
                "official_tax_effect_status": TAX_EFFECT_STATUS_STALE,
                "official_tax_effect_reason": "기준일이 오래돼서 이번 달 숫자에는 자동 반영하지 않고 재확인 대상으로 남겨요.",
                "official_tax_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in buckets["stale"]),
                "official_tax_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in buckets["stale"]
                ),
            }
        )
        return state

    if best_review:
        state.update(
            {
                "official_tax_effect_strength": TAX_EFFECT_STRENGTH_NONE,
                "official_tax_effect_source_count": len(buckets["review_needed"]),
                "official_tax_effect_status": TAX_EFFECT_STATUS_REVIEW_NEEDED,
                "official_tax_effect_reason": "검토가 더 필요한 자료라 세금 숫자에는 자동 반영하지 않았어요.",
                "official_tax_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in buckets["review_needed"]),
                "official_tax_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in buckets["review_needed"]
                ),
            }
        )
        return state

    if buckets["supported_reference"]:
        state.update(
            {
                "official_tax_effect_strength": TAX_EFFECT_STRENGTH_WEAK,
                "official_tax_effect_source_count": len(buckets["supported_reference"]),
                "official_tax_effect_status": TAX_EFFECT_STATUS_REFERENCE_ONLY,
                "official_tax_effect_reason": "사업용 카드 사용내역은 이번 단계에서 참고 정보로만 유지해요.",
                "official_tax_effect_documents": tuple(int(getattr(doc, "id", 0) or 0) for doc in buckets["supported_reference"]),
                "official_tax_effect_document_types": tuple(
                    str(getattr(doc, "document_type", "") or "") for doc in buckets["supported_reference"]
                ),
            }
        )
    return state


def collect_official_tax_effects_for_user_month(
    user_pk: int,
    *,
    month_key: str,
    today: date | None = None,
) -> dict[str, Any]:
    documents = (
        OfficialDataDocument.query.filter_by(user_pk=int(user_pk), source_system="hometax")
        .filter(
            OfficialDataDocument.document_type.in_(
                sorted(SUPPORTED_DIRECT_TAX_DOCUMENT_TYPES | SUPPORTED_REFERENCE_TAX_DOCUMENT_TYPES)
            )
        )
        .order_by(OfficialDataDocument.verified_reference_date.desc(), OfficialDataDocument.id.desc())
        .all()
    )
    return build_official_tax_effect_state(documents, month_key=month_key, today=today)


def build_official_tax_effect_notice_context(
    effect_state: dict[str, Any] | None,
    *,
    before_tax_due_krw: int | None = None,
    after_tax_due_krw: int | None = None,
) -> dict[str, Any]:
    effect = dict(effect_state or {})
    status = str(effect.get("official_tax_effect_status") or TAX_EFFECT_STATUS_NONE)
    document_types = tuple(effect.get("official_tax_effect_document_types") or ())
    document_kind_labels = _tax_document_kind_labels(
        document_types,
        status=status,
        supported_reference_count=int(effect.get("official_tax_effect_supported_reference_count") or 0),
    )
    delta = None
    if before_tax_due_krw is not None and after_tax_due_krw is not None:
        delta = int(after_tax_due_krw) - int(before_tax_due_krw)
    return {
        "show": status != TAX_EFFECT_STATUS_NONE,
        "status": status,
        "title": "홈택스 자료 반영 상태",
        "summary": str(effect.get("official_tax_effect_reason") or ""),
        "strength": str(effect.get("official_tax_effect_strength") or TAX_EFFECT_STRENGTH_NONE),
        "strength_label": STRENGTH_LABELS.get(
            str(effect.get("official_tax_effect_strength") or TAX_EFFECT_STRENGTH_NONE),
            "없음",
        ),
        "reference_date": effect.get("official_tax_reference_date"),
        "withheld_tax_krw": int(effect.get("official_withheld_tax_krw") or 0),
        "paid_tax_krw": int(effect.get("official_paid_tax_krw") or 0),
        "source_count": int(effect.get("official_tax_effect_source_count") or 0),
        "document_kind_summary": ", ".join(document_kind_labels),
        "before_tax_due_krw": int(before_tax_due_krw) if before_tax_due_krw is not None else None,
        "after_tax_due_krw": int(after_tax_due_krw) if after_tax_due_krw is not None else None,
        "delta_krw": delta,
        "recheck_required": status in {TAX_EFFECT_STATUS_STALE, TAX_EFFECT_STATUS_REVIEW_NEEDED},
    }


def build_official_tax_visual_feedback(
    effect_state: dict[str, Any] | None,
    *,
    before_tax_due_krw: int | None = None,
    after_tax_due_krw: int | None = None,
    before_buffer_target_krw: int | None = None,
    after_buffer_target_krw: int | None = None,
) -> dict[str, Any]:
    effect = dict(effect_state or {})
    status = str(effect.get("official_tax_effect_status") or TAX_EFFECT_STATUS_NONE)
    strength = str(effect.get("official_tax_effect_strength") or TAX_EFFECT_STRENGTH_NONE)
    document_types = tuple(effect.get("official_tax_effect_document_types") or ())
    supported_reference_count = int(effect.get("official_tax_effect_supported_reference_count") or 0)

    before_tax = _coerce_int(before_tax_due_krw)
    after_tax = _coerce_int(after_tax_due_krw)
    if before_buffer_target_krw is None:
        before_buffer_target_krw = before_tax
    if after_buffer_target_krw is None:
        after_buffer_target_krw = after_tax
    before_buffer = _coerce_int(before_buffer_target_krw)
    after_buffer = _coerce_int(after_buffer_target_krw)

    tax_delta = None
    if before_tax is not None and after_tax is not None:
        tax_delta = after_tax - before_tax
    buffer_delta = None
    if before_buffer is not None and after_buffer is not None:
        buffer_delta = after_buffer - before_buffer

    has_delta = any(value not in (None, 0) for value in (tax_delta, buffer_delta))
    source_labels = _tax_document_kind_labels(
        document_types,
        status=status,
        supported_reference_count=supported_reference_count,
    )
    feedback_level = _tax_feedback_level(status, strength, has_delta=has_delta)
    should_animate = status == TAX_EFFECT_STATUS_APPLIED and has_delta

    return {
        "show": status != TAX_EFFECT_STATUS_NONE,
        "status": status,
        "strength": strength,
        "strength_label": STRENGTH_LABELS.get(strength, "없음"),
        "reference_date": effect.get("official_tax_reference_date"),
        "before_tax_due_krw": before_tax,
        "after_tax_due_krw": after_tax,
        "tax_delta_krw": tax_delta,
        "before_buffer_target_krw": before_buffer,
        "after_buffer_target_krw": after_buffer,
        "buffer_delta_krw": buffer_delta,
        "feedback_level": feedback_level,
        "feedback_reason": str(effect.get("official_tax_effect_reason") or ""),
        "should_animate": should_animate,
        "source_labels": source_labels,
        "source_count": int(effect.get("official_tax_effect_source_count") or 0),
        "withheld_tax_krw": int(effect.get("official_withheld_tax_krw") or 0),
        "paid_tax_krw": int(effect.get("official_paid_tax_krw") or 0),
        "recheck_required": status in {TAX_EFFECT_STATUS_STALE, TAX_EFFECT_STATUS_REVIEW_NEEDED},
        "document_kind_summary": ", ".join(source_labels),
        "supported_reference_count": supported_reference_count,
    }


def build_official_tax_visual_feedback_for_overview(
    effect_state: dict[str, Any] | None,
    *,
    before_tax_due_krw: int | None = None,
    after_tax_due_krw: int | None = None,
) -> dict[str, Any]:
    return build_official_tax_visual_feedback(
        effect_state,
        before_tax_due_krw=before_tax_due_krw,
        after_tax_due_krw=after_tax_due_krw,
        before_buffer_target_krw=before_tax_due_krw,
        after_buffer_target_krw=after_tax_due_krw,
    )


def build_official_tax_visual_feedback_for_tax_buffer(
    effect_state: dict[str, Any] | None,
    *,
    before_tax_due_krw: int | None = None,
    after_tax_due_krw: int | None = None,
    before_buffer_target_krw: int | None = None,
    after_buffer_target_krw: int | None = None,
) -> dict[str, Any]:
    return build_official_tax_visual_feedback(
        effect_state,
        before_tax_due_krw=before_tax_due_krw,
        after_tax_due_krw=after_tax_due_krw,
        before_buffer_target_krw=before_buffer_target_krw,
        after_buffer_target_krw=after_buffer_target_krw,
    )
