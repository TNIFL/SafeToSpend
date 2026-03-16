from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from flask import current_app

from core.extensions import db
from core.time import utcnow
from domain.models import OfficialDataDocument
from services.official_data_extractors import OfficialDataFileError, build_upload_envelope
from services.official_data_guards import (
    compute_official_data_trust_grade,
    resolve_trust_fields_for_document,
    sanitize_official_data_payload_for_storage,
    sanitize_official_data_summary_for_render,
)
from services.official_data_parser_registry import (
    ALLOWED_DOCUMENT_HINTS,
    REGISTRY_STATUS_NEEDS_REVIEW,
    REGISTRY_STATUS_SUPPORTED,
    REGISTRY_STATUS_UNSUPPORTED_DOCUMENT,
    REGISTRY_STATUS_UNSUPPORTED_FORMAT,
    get_parser_for_document_type,
    identify_official_data_document,
    list_supported_document_options,
)
from services.official_data_parsers import OFFICIAL_DATA_PARSER_VERSION

PARSE_STATUS_UPLOADED = "uploaded"
PARSE_STATUS_PARSED = "parsed"
PARSE_STATUS_NEEDS_REVIEW = "needs_review"
PARSE_STATUS_UNSUPPORTED = "unsupported"
PARSE_STATUS_FAILED = "failed"
RAW_FILE_STORAGE_NONE = "none"
RAW_FILE_STORAGE_OPTIONAL_SAVED = "optional_saved"
STRUCTURE_STATUS_NOT_APPLICABLE = "not_applicable"
STRUCTURE_STATUS_PARTIAL = "partial"
STRUCTURE_STATUS_FAILED = "failed"


@dataclass(slots=True)
class OfficialDataUploadOutcome:
    document: OfficialDataDocument
    status_title: str
    status_summary: str
    status_tone: str


def list_official_data_upload_document_options() -> list[dict[str, str]]:
    return list_supported_document_options()


def _max_upload_bytes() -> int:
    return int(current_app.config.get("MAX_UPLOAD_BYTES") or (10 * 1024 * 1024))


def _normalize_hint(document_type_hint: str | None) -> str:
    hint = str(document_type_hint or "").strip()
    return hint if hint in ALLOWED_DOCUMENT_HINTS else ""


def _fallback_source_system_from_hint(hint: str) -> str:
    if hint.startswith("nhis_"):
        return "nhis"
    return "hometax"


def _fallback_display_name_from_hint(hint: str) -> str:
    options = {item["document_type"]: item["display_name"] for item in list_supported_document_options()}
    return options.get(hint, "공식 자료")


def _serialize_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def _status_copy(parse_status: str, *, error_code: str | None = None) -> tuple[str, str, str]:
    if parse_status == PARSE_STATUS_PARSED:
        return ("구조 검증 완료", "공식 양식 구조와 핵심 항목을 읽었어요. 기관 확인을 마친 자료로 단정하지 않고, 기준일 있는 자료 기준으로만 반영해요.", "success")
    if parse_status == PARSE_STATUS_NEEDS_REVIEW:
        return ("검토 필요", "일부만 읽혀서 자동 반영하지 않았어요. 기관에서 내려받은 파일을 다시 확인하거나 안내 경로를 다시 따라가 주세요.", "warn")
    if parse_status == PARSE_STATUS_UNSUPPORTED:
        if error_code == "scanned_pdf_unsupported":
            return ("지원 안 함", "사진형 PDF나 스캔 PDF는 자동 반영하지 않아요. 홈택스/NHIS에서 텍스트 추출 가능한 파일을 다시 받아 주세요.", "warn")
        return ("지원 안 함", "지원하는 형식이 아니에요. CSV, XLSX, 텍스트 추출 가능한 PDF만 처리해요.", "warn")
    return ("파싱 실패", "파일을 읽지 못했어요. 원인 코드를 확인한 뒤 공식 사이트에서 다시 받아 주세요.", "error")


def process_official_data_upload(*, user_pk: int, uploaded_file: Any, document_type_hint: str | None = None, raw_file_storage_mode: str = RAW_FILE_STORAGE_NONE) -> OfficialDataUploadOutcome:
    envelope = build_upload_envelope(uploaded_file, max_bytes=_max_upload_bytes())
    hint = _normalize_hint(document_type_hint)
    decision = identify_official_data_document(envelope, document_type_hint=hint)
    now = utcnow()
    document = OfficialDataDocument(
        user_pk=int(user_pk),
        source_system=(decision.source_system or _fallback_source_system_from_hint(hint or "hometax_withholding_statement")),
        document_type=(decision.supported_document_type or hint or "unsupported_document"),
        display_name=(decision.display_name or _fallback_display_name_from_hint(hint)),
        file_name_original=envelope.filename,
        file_mime_type=envelope.mime_type,
        file_size_bytes=int(envelope.size_bytes),
        file_hash=envelope.sha256,
        parser_version=decision.parser_version or OFFICIAL_DATA_PARSER_VERSION,
        parse_status=PARSE_STATUS_UPLOADED,
        extracted_payload_json={},
        extracted_key_summary_json={},
        trust_grade=None,
        trust_grade_label=None,
        trust_scope_label=None,
        structure_validation_status=STRUCTURE_STATUS_NOT_APPLICABLE,
        verification_source=None,
        verification_status="none",
        verification_checked_at=None,
        verification_reference_masked=None,
        user_modified_flag=False,
        sensitive_data_redacted=True,
        raw_file_storage_mode=(RAW_FILE_STORAGE_OPTIONAL_SAVED if raw_file_storage_mode == RAW_FILE_STORAGE_OPTIONAL_SAVED else RAW_FILE_STORAGE_NONE),
        raw_file_key=None,
    )

    if decision.registry_status == REGISTRY_STATUS_UNSUPPORTED_FORMAT:
        document.parse_status = PARSE_STATUS_UNSUPPORTED
        document.structure_validation_status = STRUCTURE_STATUS_NOT_APPLICABLE
        document.parse_error_code = decision.parse_error_code
        document.parse_error_detail = decision.detection_reason
        document.parsed_at = now
    elif decision.registry_status == REGISTRY_STATUS_UNSUPPORTED_DOCUMENT:
        document.parse_status = PARSE_STATUS_UNSUPPORTED
        document.structure_validation_status = STRUCTURE_STATUS_FAILED
        document.parse_error_code = decision.parse_error_code
        document.parse_error_detail = decision.detection_reason
        document.parsed_at = now
    elif decision.registry_status == REGISTRY_STATUS_NEEDS_REVIEW and not decision.supported_document_type:
        document.parse_status = PARSE_STATUS_NEEDS_REVIEW
        document.structure_validation_status = STRUCTURE_STATUS_PARTIAL
        document.parse_error_code = decision.parse_error_code
        document.parse_error_detail = decision.detection_reason
        document.extracted_payload_json = {
            "registry_detection_reason": str(decision.detection_reason or ""),
            "registry_status": str(decision.registry_status or ""),
        }
        document.parsed_at = now
    else:
        parser = get_parser_for_document_type(decision.supported_document_type or "")
        if parser is None:
            document.parse_status = PARSE_STATUS_FAILED
            document.structure_validation_status = STRUCTURE_STATUS_FAILED
            document.parse_error_code = "parser_not_registered"
            document.parse_error_detail = "지원 parser가 아직 준비되지 않았어요."
            document.parsed_at = now
        else:
            result = parser(envelope)
            document.source_system = result.source_system
            document.document_type = result.document_type
            document.display_name = result.display_name
            document.parser_version = result.parser_version
            document.parse_status = result.parse_status
            document.parse_error_code = result.parse_error_code
            document.parse_error_detail = result.parse_error_detail
            document.document_issued_at = result.document_issued_at
            document.document_period_start = result.document_period_start
            document.document_period_end = result.document_period_end
            document.verified_reference_date = result.verified_reference_date
            document.structure_validation_status = result.structure_validation_status
            document.parsed_at = now
            if decision.registry_status == REGISTRY_STATUS_NEEDS_REVIEW and decision.supported_document_type:
                document.parse_status = PARSE_STATUS_NEEDS_REVIEW
                document.structure_validation_status = STRUCTURE_STATUS_PARTIAL
                document.parse_error_code = decision.parse_error_code or document.parse_error_code
                document.parse_error_detail = decision.detection_reason
            document.extracted_payload_json = result.extracted_payload or {}
            document.extracted_key_summary_json = result.extracted_key_summary or {}

    guard = sanitize_official_data_payload_for_storage(
        document.extracted_payload_json or {},
        summary=document.extracted_key_summary_json or {},
        source_system=document.source_system,
        document_type=document.document_type,
        parse_status=document.parse_status,
    )
    document.extracted_payload_json = guard.payload
    document.extracted_key_summary_json = guard.summary
    document.sensitive_data_redacted = bool(guard.sensitive_data_redacted)
    if guard.downgraded_to_needs_review:
        document.parse_status = PARSE_STATUS_NEEDS_REVIEW
        document.structure_validation_status = STRUCTURE_STATUS_PARTIAL
        document.parse_error_code = guard.downgrade_error_code or "guard_needs_review"
        document.parse_error_detail = guard.downgrade_reason

    trust = compute_official_data_trust_grade(
        verification_source=document.verification_source,
        verification_status=document.verification_status,
        parser_parse_status=document.parse_status,
        structure_validation_status=document.structure_validation_status,
        user_modified_flag=bool(document.user_modified_flag),
    )
    document.trust_grade = trust.trust_grade
    document.trust_grade_label = trust.trust_grade_label
    document.trust_scope_label = trust.trust_scope_label

    db.session.add(document)
    db.session.commit()
    status_title, status_summary, status_tone = _status_copy(document.parse_status, error_code=document.parse_error_code)
    return OfficialDataUploadOutcome(document=document, status_title=status_title, status_summary=status_summary, status_tone=status_tone)


def get_official_data_document_for_user(*, document_id: int, user_pk: int) -> OfficialDataDocument | None:
    return OfficialDataDocument.query.filter_by(id=int(document_id), user_pk=int(user_pk)).first()


def _recheck_reason(document: OfficialDataDocument, *, today: date) -> tuple[str, str]:
    if document.parse_status != PARSE_STATUS_PARSED:
        return ("검토 필요", "자동 관리 기준으로 확정되지 않았어요.")
    reference_date = document.verified_reference_date
    if not reference_date:
        return ("검토 필요", "기준일을 확실히 읽지 못해 다시 확인이 필요해요.")
    age_days = (today - reference_date).days
    if age_days >= 120:
        return ("재확인 권장", "기준일이 오래돼서 새 시즌이나 큰 숫자 변화가 있을 때 다시 확인하는 게 좋아요.")
    if today.month in {4, 5, 6, 10, 11} and age_days >= 60:
        return ("재확인 권장", "시즌에 들어왔고 기준일이 조금 지난 자료라 한 번 더 확인하면 더 정확해져요.")
    return ("반영 가능", "현재 기준일 안에서는 구조 검증을 통과한 자료 기준으로 참고할 수 있어요.")


def build_official_data_result_context(document: OfficialDataDocument, *, today: date | None = None) -> dict[str, Any]:
    today_value = today or utcnow().date()
    status_title, status_summary, status_tone = _status_copy(document.parse_status, error_code=document.parse_error_code)
    recheck_label, recheck_detail = _recheck_reason(document, today=today_value)
    source_label = "홈택스" if document.source_system == "hometax" else "NHIS"
    summary = dict(document.extracted_key_summary_json or {})
    render_summary = sanitize_official_data_summary_for_render(summary)
    trust = resolve_trust_fields_for_document(
        trust_grade=document.trust_grade,
        trust_grade_label=document.trust_grade_label,
        trust_scope_label=document.trust_scope_label,
        verification_source=document.verification_source,
        verification_status=document.verification_status,
        parse_status=document.parse_status,
        structure_validation_status=document.structure_validation_status,
        user_modified_flag=bool(document.user_modified_flag),
        summary_fallback=summary,
    )
    summary_rows: list[dict[str, str]] = []
    for row in render_summary.rows:
        label = str(row.get("label") or "").strip()
        value = str(row.get("value") or "").strip()
        if not label:
            continue
        if label in {"기준일"}:
            value = value or _serialize_date(document.verified_reference_date) or "-"
        elif label == "기간 시작":
            continue
        elif label == "기간 종료":
            continue
        elif label == "핵심 금액":
            value = _format_krw(value)
        summary_rows.append({"label": label, "value": value or "-"})
    period_value = _format_period(
        summary.get("document_period_start") or _serialize_date(document.document_period_start),
        summary.get("document_period_end") or _serialize_date(document.document_period_end),
    )
    summary_rows.insert(
        3 if len(summary_rows) >= 3 else len(summary_rows),
        {"label": "기간", "value": period_value},
    )
    return {
        "document": document,
        "status_title": status_title,
        "status_summary": status_summary,
        "status_tone": status_tone,
        "source_label": source_label,
        "summary_rows": summary_rows,
        "recheck_label": recheck_label,
        "recheck_detail": recheck_detail,
        "trust_grade": trust.trust_grade,
        "trust_grade_label": trust.trust_grade_label,
        "trust_scope_label": trust.trust_scope_label,
        "guide_url": f"/guide/official-data#{'nhis' if document.source_system == 'nhis' else 'hometax'}",
    }


def _format_period(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start} ~ {end}"
    if start:
        return start
    if end:
        return end
    return "-"


def _format_krw(value: Any) -> str:
    try:
        if value is None or str(value).strip() == "":
            return "-"
        return f"{int(value):,}원"
    except Exception:
        return "-"
