from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.time import utcnow
from domain.models import OfficialDataDocument, TaxProfile

OFFICIAL_DATA_TAX_STALE_DAYS = 120
OFFICIAL_DATA_TAX_SEASON_STALE_DAYS = 60
OFFICIAL_DATA_NHIS_STALE_DAYS = 90
OFFICIAL_DATA_NHIS_SEASON_STALE_DAYS = 45
OFFICIAL_DATA_SEASONAL_MONTHS = {4, 5, 6, 10, 11}

SUPPORTED_TAX_DOCUMENT_TYPES = {
    "hometax_withholding_statement",
    "hometax_business_card_usage",
}
SUPPORTED_NHIS_DOCUMENT_TYPES = {
    "nhis_payment_confirmation",
}

WITHHELD_KEYS = (
    "withheld_tax_annual_krw",
    "withholding_tax_annual_krw",
    "withheld_tax_paid_annual_krw",
)
PREPAID_KEYS = (
    "prepaid_tax_annual_krw",
    "interim_prepaid_tax_annual_krw",
    "paid_tax_annual_krw",
)


@dataclass(frozen=True)
class OfficialTaxEffects:
    verified_withholding_tax_krw: int
    verified_paid_tax_krw: int
    verified_tax_reference_date: date | None
    official_data_confidence_level: str
    applied_documents: tuple[dict[str, Any], ...]
    ignored_documents: tuple[dict[str, Any], ...]
    stale_documents: tuple[dict[str, Any], ...]
    effect_messages: tuple[str, ...]
    reference_business_card_usage_krw: int
    applied_withholding_document_id: int | None
    applied_paid_tax_document_id: int | None
    manual_override_wins: bool
    priority_source: str
    verified_withholding_applied: bool
    verified_paid_tax_applied: bool


@dataclass(frozen=True)
class OfficialNhisEffects:
    verified_nhis_paid_amount_krw: int
    verified_nhis_reference_date: date | None
    official_data_confidence_level: str
    applied_documents: tuple[dict[str, Any], ...]
    ignored_documents: tuple[dict[str, Any], ...]
    stale_documents: tuple[dict[str, Any], ...]
    effect_messages: tuple[str, ...]
    nhis_official_status_label: str
    nhis_official_data_applied: bool
    nhis_recheck_recommended: bool


@dataclass(frozen=True)
class OfficialDataEffectsBundle:
    verified_withholding_tax_krw: int
    verified_paid_tax_krw: int
    verified_tax_reference_date: date | None
    verified_nhis_paid_amount_krw: int
    verified_nhis_reference_date: date | None
    official_data_confidence_level: str
    applied_documents: tuple[dict[str, Any], ...]
    ignored_documents: tuple[dict[str, Any], ...]
    stale_documents: tuple[dict[str, Any], ...]
    effect_messages: tuple[str, ...]
    tax: OfficialTaxEffects
    nhis: OfficialNhisEffects


def _empty_tax_effects() -> OfficialTaxEffects:
    return OfficialTaxEffects(
        verified_withholding_tax_krw=0,
        verified_paid_tax_krw=0,
        verified_tax_reference_date=None,
        official_data_confidence_level="low",
        applied_documents=(),
        ignored_documents=(),
        stale_documents=(),
        effect_messages=(),
        reference_business_card_usage_krw=0,
        applied_withholding_document_id=None,
        applied_paid_tax_document_id=None,
        manual_override_wins=False,
        priority_source="none",
        verified_withholding_applied=False,
        verified_paid_tax_applied=False,
    )


def _empty_nhis_effects() -> OfficialNhisEffects:
    return OfficialNhisEffects(
        verified_nhis_paid_amount_krw=0,
        verified_nhis_reference_date=None,
        official_data_confidence_level="low",
        applied_documents=(),
        ignored_documents=(),
        stale_documents=(),
        effect_messages=(),
        nhis_official_status_label="공식 자료 없음",
        nhis_official_data_applied=False,
        nhis_recheck_recommended=False,
    )


def _doc_sort_key(document: Any) -> tuple[date, datetime]:
    reference_date = _normalize_date(getattr(document, "verified_reference_date", None)) or date.min
    parsed_at = _normalize_datetime(getattr(document, "parsed_at", None)) or datetime.min
    return (reference_date, parsed_at)


def _normalize_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    return None


def _normalize_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    parsed_date = _normalize_date(text)
    return datetime.combine(parsed_date, datetime.min.time()) if parsed_date else None


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").replace("원", "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:
        return 0


def _doc_meta(document: Any, *, reason: str | None = None, effect_scope: str | None = None) -> dict[str, Any]:
    payload = dict(getattr(document, "extracted_payload_json", None) or {})
    summary = dict(getattr(document, "extracted_key_summary_json", None) or {})
    return {
        "document_id": int(getattr(document, "id", 0) or 0),
        "source_system": str(getattr(document, "source_system", "") or ""),
        "document_type": str(getattr(document, "document_type", "") or ""),
        "display_name": str(getattr(document, "display_name", "") or ""),
        "parse_status": str(getattr(document, "parse_status", "") or ""),
        "parser_version": str(getattr(document, "parser_version", "") or ""),
        "verified_reference_date": (
            _normalize_date(getattr(document, "verified_reference_date", None)).isoformat()
            if _normalize_date(getattr(document, "verified_reference_date", None))
            else None
        ),
        "document_period_start": (
            _normalize_date(getattr(document, "document_period_start", None)).isoformat()
            if _normalize_date(getattr(document, "document_period_start", None))
            else None
        ),
        "document_period_end": (
            _normalize_date(getattr(document, "document_period_end", None)).isoformat()
            if _normalize_date(getattr(document, "document_period_end", None))
            else None
        ),
        "reason": reason or "",
        "effect_scope": effect_scope or "",
        "summary_total_amount_krw": _safe_int(summary.get("total_amount_krw") or payload.get("total_withheld_tax_krw") or payload.get("total_paid_amount_krw") or payload.get("total_card_usage_krw")),
    }


def _seasonal_stale_days(*, source_system: str, today: date) -> int:
    is_seasonal = int(today.month) in OFFICIAL_DATA_SEASONAL_MONTHS
    if source_system == "nhis":
        return OFFICIAL_DATA_NHIS_SEASON_STALE_DAYS if is_seasonal else OFFICIAL_DATA_NHIS_STALE_DAYS
    return OFFICIAL_DATA_TAX_SEASON_STALE_DAYS if is_seasonal else OFFICIAL_DATA_TAX_STALE_DAYS


def _is_document_stale(document: Any, *, today: date, month_key: str | None = None) -> tuple[bool, str]:
    reference_date = _normalize_date(getattr(document, "verified_reference_date", None))
    if reference_date is None:
        return True, "missing_reference_date"
    source_system = str(getattr(document, "source_system", "") or "")
    stale_days = _seasonal_stale_days(source_system=source_system, today=today)
    if (today - reference_date).days >= stale_days:
        return True, "stale_reference_date"
    if month_key:
        try:
            target_month = datetime.strptime(str(month_key), "%Y-%m").date()
            period_end = _normalize_date(getattr(document, "document_period_end", None))
            if period_end is not None:
                grace_days = 120 if source_system == "nhis" else 400
                if period_end < (target_month - timedelta(days=grace_days)):
                    return True, "period_mismatch"
        except Exception:
            pass
    return False, ""


def _has_manual_value(profile_json: dict[str, Any] | None, keys: Sequence[str]) -> bool:
    profile = dict(profile_json or {})
    for key in keys:
        raw = profile.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool):
            return True
        if isinstance(raw, (int, float)):
            return True
        if str(raw).strip() != "":
            return True
    return False


def compute_tax_official_effects(
    documents: Iterable[Any],
    *,
    month_key: str | None = None,
    today: date | None = None,
    profile_json: dict[str, Any] | None = None,
    profile_updated_at: datetime | None = None,
) -> OfficialTaxEffects:
    today_value = today or utcnow().date()
    applied_documents: list[dict[str, Any]] = []
    ignored_documents: list[dict[str, Any]] = []
    stale_documents: list[dict[str, Any]] = []
    messages: list[str] = []

    latest_withholding_doc: Any | None = None
    latest_business_card_doc: Any | None = None

    for document in sorted(list(documents or []), key=_doc_sort_key, reverse=True):
        parse_status = str(getattr(document, "parse_status", "") or "")
        document_type = str(getattr(document, "document_type", "") or "")
        if document_type not in SUPPORTED_TAX_DOCUMENT_TYPES:
            continue
        if parse_status != "parsed":
            ignored_documents.append(_doc_meta(document, reason=f"parse_status:{parse_status}"))
            continue
        stale, stale_reason = _is_document_stale(document, today=today_value, month_key=month_key)
        if stale:
            stale_documents.append(_doc_meta(document, reason=stale_reason))
            continue
        if document_type == "hometax_withholding_statement" and latest_withholding_doc is None:
            payload = dict(getattr(document, "extracted_payload_json", None) or {})
            if _safe_int(payload.get("total_withheld_tax_krw")) <= 0 or not _normalize_date(payload.get("verified_reference_date") or getattr(document, "verified_reference_date", None)):
                ignored_documents.append(_doc_meta(document, reason="missing_verified_withholding_fields"))
                continue
            latest_withholding_doc = document
            continue
        if document_type == "hometax_business_card_usage" and latest_business_card_doc is None:
            payload = dict(getattr(document, "extracted_payload_json", None) or {})
            if _safe_int(payload.get("total_card_usage_krw")) <= 0:
                ignored_documents.append(_doc_meta(document, reason="missing_business_card_total"))
                continue
            latest_business_card_doc = document

    manual_override_wins = False
    verified_withholding_tax_krw = 0
    verified_reference_date = None
    priority_source = "none"
    applied_withholding_document_id = None

    if latest_withholding_doc is not None:
        reference_date = _normalize_date(getattr(latest_withholding_doc, "verified_reference_date", None))
        parsed_at = _normalize_datetime(getattr(latest_withholding_doc, "parsed_at", None)) or datetime.min
        if profile_updated_at and profile_updated_at > parsed_at and _has_manual_value(profile_json, WITHHELD_KEYS):
            manual_override_wins = True
            priority_source = "manual_newer_than_official"
            ignored_documents.append(_doc_meta(latest_withholding_doc, reason="manual_input_newer"))
        else:
            payload = dict(getattr(latest_withholding_doc, "extracted_payload_json", None) or {})
            verified_withholding_tax_krw = int(max(0, _safe_int(payload.get("total_withheld_tax_krw"))))
            verified_reference_date = reference_date
            applied_withholding_document_id = int(getattr(latest_withholding_doc, "id", 0) or 0)
            priority_source = "official_verified_snapshot"
            applied_documents.append(_doc_meta(latest_withholding_doc, effect_scope="tax_credit"))
            messages.append("홈택스 공식 자료 기준으로 이미 빠진 세금을 보정했어요.")

    reference_business_card_usage_krw = 0
    if latest_business_card_doc is not None:
        payload = dict(getattr(latest_business_card_doc, "extracted_payload_json", None) or {})
        reference_business_card_usage_krw = int(max(0, _safe_int(payload.get("total_card_usage_krw"))))
        applied_documents.append(_doc_meta(latest_business_card_doc, effect_scope="reference_only"))
        messages.append("사업용 카드 사용내역은 비용을 자동 확정하지 않고 참고 자료로만 유지했어요.")

    if stale_documents:
        messages.append("기준일이 지난 공식 자료는 자동 반영하지 않고 다시 확인만 권장해요.")
    if manual_override_wins:
        messages.append("더 최근 수기 입력이 있어 세금 계산은 수기 값을 우선 사용했어요.")

    confidence_level = "low"
    if verified_withholding_tax_krw > 0:
        confidence_level = "high"
    elif reference_business_card_usage_krw > 0:
        confidence_level = "medium"

    return OfficialTaxEffects(
        verified_withholding_tax_krw=int(verified_withholding_tax_krw),
        verified_paid_tax_krw=0,
        verified_tax_reference_date=verified_reference_date,
        official_data_confidence_level=confidence_level,
        applied_documents=tuple(applied_documents),
        ignored_documents=tuple(ignored_documents),
        stale_documents=tuple(stale_documents),
        effect_messages=tuple(messages),
        reference_business_card_usage_krw=int(reference_business_card_usage_krw),
        applied_withholding_document_id=applied_withholding_document_id,
        applied_paid_tax_document_id=None,
        manual_override_wins=bool(manual_override_wins),
        priority_source=priority_source,
        verified_withholding_applied=bool(verified_withholding_tax_krw > 0),
        verified_paid_tax_applied=False,
    )


def compute_nhis_official_effects(
    documents: Iterable[Any],
    *,
    month_key: str | None = None,
    today: date | None = None,
) -> OfficialNhisEffects:
    today_value = today or utcnow().date()
    applied_documents: list[dict[str, Any]] = []
    ignored_documents: list[dict[str, Any]] = []
    stale_documents: list[dict[str, Any]] = []
    messages: list[str] = []

    latest_doc: Any | None = None
    for document in sorted(list(documents or []), key=_doc_sort_key, reverse=True):
        document_type = str(getattr(document, "document_type", "") or "")
        if document_type not in SUPPORTED_NHIS_DOCUMENT_TYPES:
            continue
        parse_status = str(getattr(document, "parse_status", "") or "")
        if parse_status != "parsed":
            ignored_documents.append(_doc_meta(document, reason=f"parse_status:{parse_status}"))
            continue
        stale, stale_reason = _is_document_stale(document, today=today_value, month_key=month_key)
        if stale:
            stale_documents.append(_doc_meta(document, reason=stale_reason))
            continue
        payload = dict(getattr(document, "extracted_payload_json", None) or {})
        if _safe_int(payload.get("total_paid_amount_krw")) <= 0 or not _normalize_date(payload.get("verified_reference_date") or getattr(document, "verified_reference_date", None)):
            ignored_documents.append(_doc_meta(document, reason="missing_nhis_core_values"))
            continue
        latest_doc = document
        break

    if latest_doc is None:
        label = "재확인 권장" if stale_documents else "공식 자료 없음"
        if stale_documents:
            messages.append("건보료 공식 자료 기준일이 지나 다시 확인이 필요해요.")
        return OfficialNhisEffects(
            verified_nhis_paid_amount_krw=0,
            verified_nhis_reference_date=None,
            official_data_confidence_level=("medium" if stale_documents else "low"),
            applied_documents=(),
            ignored_documents=tuple(ignored_documents),
            stale_documents=tuple(stale_documents),
            effect_messages=tuple(messages),
            nhis_official_status_label=label,
            nhis_official_data_applied=False,
            nhis_recheck_recommended=bool(stale_documents),
        )

    payload = dict(getattr(latest_doc, "extracted_payload_json", None) or {})
    verified_paid_amount_krw = int(max(0, _safe_int(payload.get("total_paid_amount_krw"))))
    verified_reference_date = _normalize_date(payload.get("verified_reference_date") or getattr(latest_doc, "verified_reference_date", None))
    applied_documents.append(_doc_meta(latest_doc, effect_scope="nhis_reference"))
    messages.append("건보료 공식 자료 기준일을 함께 표시하고 신뢰도 판단에 반영했어요.")
    return OfficialNhisEffects(
        verified_nhis_paid_amount_krw=verified_paid_amount_krw,
        verified_nhis_reference_date=verified_reference_date,
        official_data_confidence_level="high",
        applied_documents=tuple(applied_documents),
        ignored_documents=tuple(ignored_documents),
        stale_documents=tuple(stale_documents),
        effect_messages=tuple(messages),
        nhis_official_status_label="공식 자료 기준 확인",
        nhis_official_data_applied=True,
        nhis_recheck_recommended=False,
    )


def collect_official_data_effects_for_user(
    session: Any,
    *,
    user_pk: int,
    month_key: str | None = None,
    today: date | None = None,
    profile_json: dict[str, Any] | None = None,
    profile_updated_at: datetime | None = None,
) -> OfficialDataEffectsBundle:
    try:
        docs = (
            OfficialDataDocument.query.filter_by(user_pk=int(user_pk))
            .order_by(OfficialDataDocument.verified_reference_date.desc(), OfficialDataDocument.parsed_at.desc(), OfficialDataDocument.id.desc())
            .limit(50)
            .all()
        )
        profile_row = None
        if profile_updated_at is None or profile_json is None:
            profile_row = TaxProfile.query.filter_by(user_pk=int(user_pk)).first()
        profile_payload = dict(profile_json or (getattr(profile_row, "profile_json", None) or {}))
        profile_updated = profile_updated_at or _normalize_datetime(getattr(profile_row, "updated_at", None))
        tax = compute_tax_official_effects(
            docs,
            month_key=month_key,
            today=today,
            profile_json=profile_payload,
            profile_updated_at=profile_updated,
        )
        nhis = compute_nhis_official_effects(docs, month_key=month_key, today=today)
    except Exception:
        tax = _empty_tax_effects()
        nhis = _empty_nhis_effects()

    applied_documents = tuple([*tax.applied_documents, *nhis.applied_documents])
    ignored_documents = tuple([*tax.ignored_documents, *nhis.ignored_documents])
    stale_documents = tuple([*tax.stale_documents, *nhis.stale_documents])
    effect_messages = tuple([*tax.effect_messages, *nhis.effect_messages])
    confidence_level = "low"
    if tax.official_data_confidence_level == "high" or nhis.official_data_confidence_level == "high":
        confidence_level = "high"
    elif tax.official_data_confidence_level == "medium" or nhis.official_data_confidence_level == "medium":
        confidence_level = "medium"

    return OfficialDataEffectsBundle(
        verified_withholding_tax_krw=int(tax.verified_withholding_tax_krw),
        verified_paid_tax_krw=int(tax.verified_paid_tax_krw),
        verified_tax_reference_date=tax.verified_tax_reference_date,
        verified_nhis_paid_amount_krw=int(nhis.verified_nhis_paid_amount_krw),
        verified_nhis_reference_date=nhis.verified_nhis_reference_date,
        official_data_confidence_level=confidence_level,
        applied_documents=applied_documents,
        ignored_documents=ignored_documents,
        stale_documents=stale_documents,
        effect_messages=effect_messages,
        tax=tax,
        nhis=nhis,
    )


def summarize_official_data_effects(
    *,
    tax_estimate: Any | None = None,
    nhis_result_meta: dict[str, Any] | None = None,
    document: OfficialDataDocument | None = None,
) -> dict[str, Any]:
    if document is not None:
        reference_date = _normalize_date(getattr(document, "verified_reference_date", None))
        parse_status = str(getattr(document, "parse_status", "") or "")
        if parse_status == "parsed":
            title = "공식 자료 기준 스냅샷을 저장했어요"
            summary = "이제 이 자료의 기준일과 핵심값을 자동 관리 기준으로 참고할 수 있어요."
            tone = "success"
        elif parse_status == "needs_review":
            title = "공식 자료를 일부만 읽었어요"
            summary = "자동 반영은 하지 않았고, 어떤 파일을 다시 받아야 하는지 안내를 함께 보여드려요."
            tone = "warn"
        else:
            title = "공식 자료 자동 반영은 하지 않았어요"
            summary = "지원 형식이나 문서 구조를 다시 확인한 뒤 원본 파일을 다시 받아 주세요."
            tone = "warn"
        bullets = []
        if reference_date:
            bullets.append(f"기준일: {reference_date.isoformat()}")
        if document.document_type == "hometax_withholding_statement" and parse_status == "parsed":
            bullets.append("이미 빠진 세금 기준값 보정에 사용할 수 있어요.")
        if document.document_type == "nhis_payment_confirmation" and parse_status == "parsed":
            bullets.append("건보료 기준일과 납부 확인 상태를 더 분명하게 보여줄 수 있어요.")
        if document.document_type == "hometax_business_card_usage":
            bullets.append("사업용 카드 사용내역은 자동 비용 확정이 아니라 참고 자료로만 유지해요.")
        return {
            "show": True,
            "tone": tone,
            "title": title,
            "summary": summary,
            "bullets": tuple(bullets),
            "reference_date": reference_date.isoformat() if reference_date else None,
        }

    est = tax_estimate
    nhis_meta = dict(nhis_result_meta or {})
    official_tax_applied = bool(getattr(est, "official_data_applied", False)) if est is not None else False
    official_tax_reference_date = _normalize_date(getattr(est, "official_tax_reference_date", None)) if est is not None else None
    tax_delta = int(getattr(est, "tax_delta_from_official_data_krw", 0) or 0) if est is not None else 0
    nhis_applied = bool(nhis_meta.get("nhis_official_data_applied"))
    nhis_reference_date = _normalize_date(nhis_meta.get("nhis_official_reference_date"))
    nhis_recheck = bool(nhis_meta.get("nhis_recheck_recommended"))
    if not (official_tax_applied or nhis_applied or official_tax_reference_date or nhis_reference_date or nhis_recheck):
        return {"show": False}

    bullets: list[str] = []
    tone = "success"
    title = "공식 자료 기준으로 보정했어요"
    summary = "공식 자료 기준일과 확인된 값을 함께 참고하고 있어요."
    if official_tax_applied:
        bullets.append("이미 빠진 세금을 공식 자료 기준으로 반영했어요.")
        if tax_delta:
            bullets.append(f"공식 자료 반영으로 예상세금 차이가 {abs(tax_delta):,}원 생겼어요.")
        if official_tax_reference_date:
            bullets.append(f"세금 기준일: {official_tax_reference_date.isoformat()}")
    if nhis_applied:
        bullets.append("건보료는 공식 자료 기준일과 납부 확인 상태를 함께 보여주고 있어요.")
        if nhis_reference_date:
            bullets.append(f"건보 기준일: {nhis_reference_date.isoformat()}")
    if nhis_recheck and not nhis_applied:
        tone = "warn"
        title = "공식 자료를 다시 확인하면 더 정확해져요"
        summary = "기준일이 지난 자료는 자동 확정하지 않고 다시 확인만 권장해요."
        if nhis_reference_date:
            bullets.append(f"최근 건보 자료 기준일: {nhis_reference_date.isoformat()}")
    return {
        "show": True,
        "tone": tone,
        "title": title,
        "summary": summary,
        "bullets": tuple(bullets),
        "reference_date": (
            official_tax_reference_date.isoformat() if official_tax_reference_date else (nhis_reference_date.isoformat() if nhis_reference_date else None)
        ),
    }
