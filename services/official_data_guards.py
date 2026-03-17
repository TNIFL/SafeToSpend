from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

TRUST_GRADE_A = "A"
TRUST_GRADE_B = "B"
TRUST_GRADE_C = "C"
TRUST_GRADE_D = "D"

TRUST_GRADE_LABELS = {
    TRUST_GRADE_A: "기관 확인 완료",
    TRUST_GRADE_B: "공식 양식 구조와 일치",
    TRUST_GRADE_C: "업로드한 자료 기준",
    TRUST_GRADE_D: "검토 필요",
}

TRUST_GRADE_SCOPE_LABELS = {
    TRUST_GRADE_A: "공식 기관 확인 메타가 있는 자료",
    TRUST_GRADE_B: "기관 확인 전 구조 검증 자료",
    TRUST_GRADE_C: "사용자 업로드 자료 기준",
    TRUST_GRADE_D: "검토나 수정이 더 필요한 자료",
}

ALLOWED_VERIFICATION_SOURCES = {
    "government24_download_verify",
    "hometax_origin_check",
    "nhis_certificate_verify",
}
ALLOWED_VERIFICATION_STATUSES = {"verified", "success", "completed"}

DISALLOWED_TEXT_KEYS = {
    "preview_text",
    "text_preview",
    "raw_text",
    "raw_content",
    "document_text",
    "content_text",
    "snippet",
    "body_text",
    "full_text",
}
IDENTIFIER_KEYS = {"payor_key", "business_key", "insured_key"}
REMOVABLE_NHIS_KEYS = {"member_type"}
SUMMARY_BASE_FIELDS = {
    "issuer",
    "document_name",
    "verified_reference_date",
    "document_period_start",
    "document_period_end",
    "total_amount_krw",
}
MAX_SAFE_DETAIL_CHARS = 120
RRN_PATTERN = re.compile(r"(?<!\d)(\d{6})[- ]?(\d{7})(?!\d)")
HEALTH_DETAIL_KEYWORDS = {
    "상병",
    "질환",
    "병명",
    "진단",
    "진료",
    "치료",
    "입원",
    "통원",
    "수술",
    "처방",
    "약제",
    "검사결과",
}


@dataclass(slots=True)
class OfficialDataGuardDecision:
    payload: dict[str, Any]
    summary: dict[str, Any]
    removed_fields: tuple[str, ...]
    masked_fields: tuple[str, ...]
    downgraded_to_needs_review: bool
    downgrade_reason: str | None
    downgrade_error_code: str | None
    trust_grade: str
    trust_grade_label: str
    trust_scope_label: str


@dataclass(slots=True)
class OfficialDataRenderSummary:
    rows: tuple[dict[str, str], ...]
    trust_grade: str
    trust_grade_label: str
    trust_scope_label: str


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask_value(value: str) -> str:
    text = _stringify(value)
    if not text:
        return ""
    if RRN_PATTERN.search(text):
        return "******-*******"
    if len(text) <= 4:
        return "*" * len(text)
    return f"***{text[-4:]}"


def classify_sensitive_tokens(*values: Any) -> dict[str, bool]:
    tags = {
        "resident_registration_number": False,
        "health_detail_text": False,
        "long_preview_text": False,
    }
    for value in values:
        text = _stringify(value)
        if not text:
            continue
        if RRN_PATTERN.search(text):
            tags["resident_registration_number"] = True
        lowered = text.lower()
        if any(keyword in text for keyword in HEALTH_DETAIL_KEYWORDS) or "diagnosis" in lowered:
            tags["health_detail_text"] = True
        if len(text) > MAX_SAFE_DETAIL_CHARS and ("\n" in text or "|" in text or "  " in text):
            tags["long_preview_text"] = True
    return tags


def reject_disallowed_official_data_fields(payload: dict[str, Any] | None, *, source_system: str | None = None) -> dict[str, Any]:
    body = dict(payload or {})
    removed_fields: list[str] = []
    blocked_reasons: list[str] = []
    downgraded = False
    for key, value in body.items():
        key_name = str(key or "").strip()
        if not key_name:
            continue
        normalized = key_name.lower()
        if key_name in DISALLOWED_TEXT_KEYS or normalized.endswith("_preview") or normalized.endswith("_text"):
            removed_fields.append(key_name)
            blocked_reasons.append("raw_preview_disallowed")
            continue
        if key_name == "fixture_source":
            removed_fields.append(key_name)
            continue
        if source_system == "nhis" and key_name in REMOVABLE_NHIS_KEYS:
            removed_fields.append(key_name)
            continue
        sensitive = classify_sensitive_tokens(value)
        if sensitive["resident_registration_number"]:
            removed_fields.append(key_name)
            blocked_reasons.append("resident_registration_number_detected")
            downgraded = True
            continue
        if sensitive["health_detail_text"] and isinstance(value, str):
            removed_fields.append(key_name)
            blocked_reasons.append("health_detail_text_disallowed")
            downgraded = True
            continue
        if sensitive["long_preview_text"] and isinstance(value, str):
            removed_fields.append(key_name)
            blocked_reasons.append("long_preview_disallowed")
    return {
        "removed_fields": tuple(dict.fromkeys(removed_fields)),
        "blocked_reasons": tuple(dict.fromkeys(blocked_reasons)),
        "downgraded_to_needs_review": bool(downgraded),
    }


def compute_official_data_trust_grade(
    *,
    verification_source: str | None = None,
    verification_status: str | None = None,
    parser_parse_status: str | None = None,
    structure_validation_result: str | bool | None = None,
    user_modified_flag: bool = False,
) -> tuple[str, str, str]:
    source = _stringify(verification_source)
    status = _stringify(verification_status).lower()
    parse_status = _stringify(parser_parse_status)
    structure = _stringify(structure_validation_result)

    if user_modified_flag:
        grade = TRUST_GRADE_D
    elif source in ALLOWED_VERIFICATION_SOURCES and status in ALLOWED_VERIFICATION_STATUSES and parse_status == "parsed":
        grade = TRUST_GRADE_A
    elif parse_status == "parsed" and structure in {"supported_document_type", "parsed", "passed", "true"}:
        grade = TRUST_GRADE_B
    elif parse_status in {"needs_review", "failed"}:
        grade = TRUST_GRADE_D
    else:
        grade = TRUST_GRADE_C
    return grade, TRUST_GRADE_LABELS[grade], TRUST_GRADE_SCOPE_LABELS[grade]


def sanitize_official_data_payload_for_storage(
    payload: dict[str, Any] | None,
    *,
    summary: dict[str, Any] | None = None,
    source_system: str,
    document_type: str,
    parse_status: str,
    verification_source: str | None = None,
    verification_status: str | None = None,
    user_modified_flag: bool = False,
) -> OfficialDataGuardDecision:
    raw_payload = dict(payload or {})
    raw_summary = dict(summary or {})

    rejected = reject_disallowed_official_data_fields(raw_payload, source_system=source_system)
    removed_fields = list(rejected["removed_fields"])
    blocked_reasons = list(rejected["blocked_reasons"])
    masked_fields: list[str] = []
    clean_payload: dict[str, Any] = {}

    for key, value in raw_payload.items():
        if key in rejected["removed_fields"]:
            continue
        if key in IDENTIFIER_KEYS:
            text = _stringify(value)
            if not text:
                continue
            clean_payload[f"{key}_hash"] = _hash_value(text)
            clean_payload[f"{key}_masked"] = _mask_value(text)
            masked_fields.append(key)
            continue
        if isinstance(value, str):
            sensitive = classify_sensitive_tokens(value)
            if sensitive["resident_registration_number"] or sensitive["health_detail_text"]:
                removed_fields.append(key)
                blocked_reasons.append("disallowed_string_value")
                continue
            if len(value) > MAX_SAFE_DETAIL_CHARS and ("\n" in value or "|" in value):
                removed_fields.append(key)
                blocked_reasons.append("long_text_removed")
                continue
        clean_payload[key] = value

    clean_summary: dict[str, Any] = {}
    for key in SUMMARY_BASE_FIELDS:
        if key in raw_summary:
            clean_summary[key] = raw_summary.get(key)

    identifier_label = _stringify(raw_summary.get("primary_key_label")) or "식별 참조"
    identifier_value = _stringify(raw_summary.get("primary_key_value"))
    if identifier_value:
        if classify_sensitive_tokens(identifier_value)["resident_registration_number"]:
            blocked_reasons.append("summary_identifier_rrn_removed")
            removed_fields.append("primary_key_value")
        else:
            clean_summary["primary_key_label"] = identifier_label.replace("식별키", "식별 참조")
            clean_summary["primary_key_value"] = _mask_value(identifier_value)
            masked_fields.append("primary_key_value")
    else:
        for identifier_key in IDENTIFIER_KEYS:
            masked_key = f"{identifier_key}_masked"
            if masked_key in clean_payload:
                clean_summary["primary_key_label"] = identifier_label.replace("식별키", "식별 참조")
                clean_summary["primary_key_value"] = clean_payload[masked_key]
                masked_fields.append(identifier_key)
                break

    trust_grade, trust_grade_label, trust_scope_label = compute_official_data_trust_grade(
        verification_source=verification_source,
        verification_status=verification_status,
        parser_parse_status=parse_status,
        structure_validation_result=("supported_document_type" if parse_status == "parsed" else parse_status),
        user_modified_flag=user_modified_flag,
    )
    clean_summary["trust_grade"] = trust_grade
    clean_summary["trust_grade_label"] = trust_grade_label
    clean_summary["trust_scope_label"] = trust_scope_label
    clean_summary["document_type"] = document_type
    clean_summary["source_system"] = source_system
    clean_payload["trust_grade"] = trust_grade
    clean_payload["trust_grade_label"] = trust_grade_label
    clean_payload["trust_scope_label"] = trust_scope_label
    clean_payload["storage_guard_version"] = "official_data_guard_v1"

    downgraded = bool(rejected["downgraded_to_needs_review"])
    downgrade_reason = None
    downgrade_error_code = None
    if downgraded:
        downgrade_reason = "저장 금지 데이터가 감지돼 자동 반영을 중단했어요."
        downgrade_error_code = "disallowed_sensitive_content"
        trust_grade = TRUST_GRADE_D
        trust_grade_label = TRUST_GRADE_LABELS[trust_grade]
        trust_scope_label = TRUST_GRADE_SCOPE_LABELS[trust_grade]
        clean_summary["trust_grade"] = trust_grade
        clean_summary["trust_grade_label"] = trust_grade_label
        clean_summary["trust_scope_label"] = trust_scope_label
        clean_payload["trust_grade"] = trust_grade
        clean_payload["trust_grade_label"] = trust_grade_label
        clean_payload["trust_scope_label"] = trust_scope_label

    return OfficialDataGuardDecision(
        payload=clean_payload,
        summary=clean_summary,
        removed_fields=tuple(dict.fromkeys(removed_fields)),
        masked_fields=tuple(dict.fromkeys(masked_fields)),
        downgraded_to_needs_review=downgraded,
        downgrade_reason=downgrade_reason,
        downgrade_error_code=downgrade_error_code,
        trust_grade=trust_grade,
        trust_grade_label=trust_grade_label,
        trust_scope_label=trust_scope_label,
    )


def sanitize_official_data_summary_for_render(summary: dict[str, Any] | None) -> OfficialDataRenderSummary:
    body = dict(summary or {})
    rows: list[dict[str, str]] = []
    field_map = (
        ("issuer", "발급기관"),
        ("document_name", "문서명"),
        ("verified_reference_date", "기준일"),
        ("document_period_start", "기간 시작"),
        ("document_period_end", "기간 종료"),
        ("total_amount_krw", "핵심 금액"),
    )
    for key, label in field_map:
        value = body.get(key)
        if value in (None, ""):
            continue
        rows.append({"label": label, "value": str(value)})
    identifier_value = _stringify(body.get("primary_key_value"))
    if identifier_value:
        rows.append({"label": _stringify(body.get("primary_key_label")) or "식별 참조", "value": _mask_value(identifier_value)})
    return OfficialDataRenderSummary(
        rows=tuple(rows),
        trust_grade=_stringify(body.get("trust_grade")) or TRUST_GRADE_C,
        trust_grade_label=_stringify(body.get("trust_grade_label")) or TRUST_GRADE_LABELS[TRUST_GRADE_C],
        trust_scope_label=_stringify(body.get("trust_scope_label")) or TRUST_GRADE_SCOPE_LABELS[TRUST_GRADE_C],
    )
