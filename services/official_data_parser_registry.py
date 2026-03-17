from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from services.official_data_extractors import (
    OfficialDataFileEnvelope,
    build_envelope_from_path,
    extract_matrix,
    extract_preview_text,
    is_supported_extension,
    is_supported_mime_for_extension,
    pdf_is_encrypted,
    pdf_looks_like_scanned_image,
)
from services.official_data_parsers import (
    DOCUMENT_ISSUER_VARIANTS,
    DOCUMENT_TITLE_VARIANTS,
    OFFICIAL_DATA_PARSER_VERSION,
    TABULAR_HEADER_ALIASES,
    parse_hometax_business_card_usage,
    parse_hometax_tax_payment_history,
    parse_hometax_withholding_statement,
    parse_nhis_eligibility_status,
    parse_nhis_payment_confirmation,
)

REGISTRY_STATUS_SUPPORTED = "supported_document_type"
REGISTRY_STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
REGISTRY_STATUS_UNSUPPORTED_DOCUMENT = "unsupported_document_type"
REGISTRY_STATUS_NEEDS_REVIEW = "needs_review"

FORBIDDEN_FORMAT_ERROR_CODES = {
    "scanned_pdf_unsupported",
    "encrypted_pdf_unsupported",
    "unsupported_extension",
    "unsupported_mime_type",
}

SUPPORTED_DOCUMENT_SPECS: dict[str, dict[str, Any]] = {
    "hometax_withholding_statement": {
        "source_system": "hometax",
        "display_name": "이미 빠진 세금/원천징수 자료",
        "supported_extensions": {".csv", ".xlsx"},
        "required_headers": {"문서명", "발급기관", "기준일", "귀속기간시작", "귀속기간종료", "총 원천징수세액", "지급처 식별키"},
        "header_aliases": TABULAR_HEADER_ALIASES["hometax_withholding_statement"],
        "title_variants": DOCUMENT_TITLE_VARIANTS["hometax_withholding_statement"],
        "issuer_variants": DOCUMENT_ISSUER_VARIANTS["hometax_withholding_statement"],
        "parser": parse_hometax_withholding_statement,
        "guide_anchor": "hometax",
    },
    "hometax_business_card_usage": {
        "source_system": "hometax",
        "display_name": "사업용 카드 사용내역",
        "supported_extensions": {".csv", ".xlsx"},
        "required_headers": {"문서명", "발급기관", "기준일", "사용기간시작", "사용기간종료", "총 사용금액", "사업자 식별키"},
        "header_aliases": TABULAR_HEADER_ALIASES["hometax_business_card_usage"],
        "title_variants": DOCUMENT_TITLE_VARIANTS["hometax_business_card_usage"],
        "issuer_variants": DOCUMENT_ISSUER_VARIANTS["hometax_business_card_usage"],
        "parser": parse_hometax_business_card_usage,
        "guide_anchor": "hometax",
    },
    "hometax_tax_payment_history": {
        "source_system": "hometax",
        "display_name": "홈택스 납부내역",
        "supported_extensions": {".csv", ".xlsx", ".pdf"},
        "required_headers": {"문서명", "발급기관", "조회일", "세목", "납부일", "납부세액 합계", "귀속기간시작", "귀속기간종료"},
        "header_aliases": TABULAR_HEADER_ALIASES["hometax_tax_payment_history"],
        "title_variants": DOCUMENT_TITLE_VARIANTS["hometax_tax_payment_history"],
        "issuer_variants": DOCUMENT_ISSUER_VARIANTS["hometax_tax_payment_history"],
        "partial_tokens": {"납부", "세목", "조회일", "납부세액"},
        "parser": parse_hometax_tax_payment_history,
        "guide_anchor": "hometax",
    },
    "nhis_payment_confirmation": {
        "source_system": "nhis",
        "display_name": "건보료 납부확인서",
        "supported_extensions": {".pdf"},
        "title_variants": DOCUMENT_TITLE_VARIANTS["nhis_payment_confirmation"],
        "issuer_variants": DOCUMENT_ISSUER_VARIANTS["nhis_payment_confirmation"],
        "partial_tokens": {"보험료", "납부", "가입자 구분"},
        "parser": parse_nhis_payment_confirmation,
        "guide_anchor": "nhis",
    },
    "nhis_eligibility_status": {
        "source_system": "nhis",
        "display_name": "NHIS 자격 상태 자료",
        "supported_extensions": {".pdf"},
        "title_variants": DOCUMENT_TITLE_VARIANTS["nhis_eligibility_status"],
        "issuer_variants": DOCUMENT_ISSUER_VARIANTS["nhis_eligibility_status"],
        "partial_tokens": {"자격", "취득일", "상실일", "가입자 유형"},
        "parser": parse_nhis_eligibility_status,
        "guide_anchor": "nhis",
    },
}

ALLOWED_DOCUMENT_HINTS = set(SUPPORTED_DOCUMENT_SPECS.keys())


@dataclass(slots=True)
class OfficialDataRegistryDecision:
    registry_status: str
    supported_document_type: str | None
    source_system: str | None
    display_name: str | None
    parser_version: str
    parse_error_code: str | None
    detection_reason: str
    preview_text: str
    guide_anchor: str | None = None


def list_supported_document_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for document_type, spec in SUPPORTED_DOCUMENT_SPECS.items():
        options.append(
            {
                "document_type": document_type,
                "display_name": spec["display_name"],
                "source_system": spec["source_system"],
                "guide_anchor": spec["guide_anchor"],
            }
        )
    return options


def get_parser_for_document_type(document_type: str) -> Callable[[OfficialDataFileEnvelope], Any] | None:
    spec = SUPPORTED_DOCUMENT_SPECS.get(str(document_type or "").strip())
    if not spec:
        return None
    return spec.get("parser")


def _preview_headers(envelope: OfficialDataFileEnvelope) -> set[str]:
    matrix = extract_matrix(envelope)
    if not matrix:
        return set()
    return {str(x or "").strip() for row in matrix[:3] for x in row if str(x or "").strip()}


def _normalize_fragment(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or ("가" <= ch <= "힣"))


def _contains_variant(text: str, variants: tuple[str, ...]) -> bool:
    normalized = _normalize_fragment(text)
    return any(_normalize_fragment(variant) in normalized for variant in variants if str(variant or "").strip())


def _header_matches_alias(header: str, alias: str) -> bool:
    header_norm = _normalize_fragment(header)
    alias_norm = _normalize_fragment(alias)
    if not header_norm or not alias_norm:
        return False
    return header_norm == alias_norm or header_norm.startswith(alias_norm)


def _tabular_alias_match_count(envelope: OfficialDataFileEnvelope, spec: dict[str, Any]) -> int:
    matrix = extract_matrix(envelope)
    if not matrix:
        return 0
    alias_map = dict(spec.get("header_aliases") or {})
    required_headers = set(spec.get("required_headers") or set())
    scoped_alias_map = {key: value for key, value in alias_map.items() if key in required_headers} or alias_map
    if not scoped_alias_map:
        return 0
    best_score = 0
    for row in matrix[:3]:
        headers = [str(cell or "").strip() for cell in row]
        score = 0
        for aliases in scoped_alias_map.values():
            if any(any(_header_matches_alias(header, alias) for alias in aliases) for header in headers):
                score += 1
        best_score = max(best_score, score)
    return best_score


def _matches_tabular_spec(envelope: OfficialDataFileEnvelope, spec: dict[str, Any], preview_text: str) -> bool:
    alias_map = dict(spec.get("header_aliases") or {})
    required_headers = set(spec.get("required_headers") or set())
    if alias_map:
        return _tabular_alias_match_count(envelope, spec) >= len(required_headers or alias_map)
    headers = _preview_headers(envelope)
    if required_headers:
        return required_headers.issubset(headers)
    title_variants = tuple(spec.get("title_variants") or ())
    issuer_variants = tuple(spec.get("issuer_variants") or ())
    return bool(
        title_variants
        and issuer_variants
        and _contains_variant(preview_text, title_variants)
        and _contains_variant(preview_text, issuer_variants)
    )


def _matches_pdf_spec(preview_text: str, spec: dict[str, Any]) -> bool:
    title_variants = tuple(spec.get("title_variants") or ())
    issuer_variants = tuple(spec.get("issuer_variants") or ())
    return bool(
        title_variants
        and issuer_variants
        and _contains_variant(preview_text, title_variants)
        and _contains_variant(preview_text, issuer_variants)
    )


def _tabular_partial_match_score(envelope: OfficialDataFileEnvelope, spec: dict[str, Any], preview_text: str) -> int:
    required_headers = set(spec.get("required_headers") or set())
    alias_score = _tabular_alias_match_count(envelope, spec)
    overlap = alias_score
    if not overlap and required_headers:
        headers = _preview_headers(envelope)
        overlap = len(required_headers & headers)
    partial_tokens = set(spec.get("partial_tokens") or set())
    token_hits = sum(1 for token in partial_tokens if _contains_variant(preview_text, (token,)))
    if required_headers and 0 < overlap < len(required_headers):
        return overlap + token_hits
    title_variants = tuple(spec.get("title_variants") or ())
    issuer_variants = tuple(spec.get("issuer_variants") or ())
    text_hits = int(bool(title_variants and _contains_variant(preview_text, title_variants))) + int(
        bool(issuer_variants and _contains_variant(preview_text, issuer_variants))
    )
    if not required_headers and text_hits:
        return text_hits + token_hits
    return 0


def _pdf_partial_match_score(preview_text: str, spec: dict[str, Any]) -> int:
    partial_tokens = set(spec.get("partial_tokens") or set(spec.get("must_contain") or set()))
    token_hits = sum(1 for token in partial_tokens if _contains_variant(preview_text, (token,)))
    title_variants = tuple(spec.get("title_variants") or ())
    issuer_variants = tuple(spec.get("issuer_variants") or ())
    token_hits += int(bool(title_variants and _contains_variant(preview_text, title_variants)))
    token_hits += int(bool(issuer_variants and _contains_variant(preview_text, issuer_variants)))
    if token_hits > 0:
        return token_hits
    return 0


def identify_official_data_document(envelope: OfficialDataFileEnvelope, *, document_type_hint: str | None = None) -> OfficialDataRegistryDecision:
    hint = str(document_type_hint or "").strip()
    preview_text = extract_preview_text(envelope)
    if not is_supported_extension(envelope.ext):
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_UNSUPPORTED_FORMAT,
            supported_document_type=None,
            source_system=None,
            display_name=None,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="unsupported_extension",
            detection_reason="CSV, XLSX, 텍스트 추출 가능한 PDF만 지원해요.",
            preview_text=preview_text,
        )
    if not is_supported_mime_for_extension(envelope.ext, envelope.mime_type):
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_UNSUPPORTED_FORMAT,
            supported_document_type=None,
            source_system=None,
            display_name=None,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="unsupported_mime_type",
            detection_reason="지원하는 공식 원본 형식이 아니에요.",
            preview_text=preview_text,
        )
    if envelope.ext == ".pdf" and pdf_is_encrypted(envelope.raw_bytes):
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_UNSUPPORTED_FORMAT,
            supported_document_type=None,
            source_system=None,
            display_name=None,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="encrypted_pdf_unsupported",
            detection_reason="암호가 걸린 PDF는 지원하지 않아요.",
            preview_text=preview_text,
        )
    if envelope.ext == ".pdf" and pdf_looks_like_scanned_image(envelope.raw_bytes):
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_UNSUPPORTED_FORMAT,
            supported_document_type=None,
            source_system=None,
            display_name=None,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="scanned_pdf_unsupported",
            detection_reason="스캔 PDF나 사진형 PDF는 자동 반영하지 않아요.",
            preview_text=preview_text,
        )

    matched_document_type: str | None = None
    for document_type, spec in SUPPORTED_DOCUMENT_SPECS.items():
        if envelope.ext not in set(spec.get("supported_extensions") or set()):
            continue
        matched = _matches_pdf_spec(preview_text, spec) if envelope.ext == ".pdf" else _matches_tabular_spec(envelope, spec, preview_text)
        if matched:
            matched_document_type = document_type
            break

    if matched_document_type:
        spec = SUPPORTED_DOCUMENT_SPECS[matched_document_type]
        if hint and hint in ALLOWED_DOCUMENT_HINTS and hint != matched_document_type:
            return OfficialDataRegistryDecision(
                registry_status=REGISTRY_STATUS_NEEDS_REVIEW,
                supported_document_type=matched_document_type,
                source_system=spec["source_system"],
                display_name=spec["display_name"],
                parser_version=OFFICIAL_DATA_PARSER_VERSION,
                parse_error_code="document_type_mismatch",
                detection_reason="선택한 자료 종류와 실제 문서 헤더가 달라 보여요.",
                preview_text=preview_text,
                guide_anchor=spec.get("guide_anchor"),
            )
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_SUPPORTED,
            supported_document_type=matched_document_type,
            source_system=spec["source_system"],
            display_name=spec["display_name"],
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code=None,
            detection_reason="지원 문서 헤더를 확인했어요.",
            preview_text=preview_text,
            guide_anchor=spec.get("guide_anchor"),
        )

    partial_match_document_type: str | None = None
    partial_match_score = 0
    for document_type, spec in SUPPORTED_DOCUMENT_SPECS.items():
        if envelope.ext not in set(spec.get("supported_extensions") or set()):
            continue
        score = (
            _pdf_partial_match_score(preview_text, spec)
            if envelope.ext == ".pdf"
            else _tabular_partial_match_score(envelope, spec, preview_text)
        )
        if score > partial_match_score:
            partial_match_score = score
            partial_match_document_type = document_type

    if partial_match_document_type:
        spec = SUPPORTED_DOCUMENT_SPECS[partial_match_document_type]
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_NEEDS_REVIEW,
            supported_document_type=partial_match_document_type,
            source_system=spec["source_system"],
            display_name=spec["display_name"],
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="partial_structure_detected",
            detection_reason="공식 자료처럼 보이지만 필수 구조를 끝까지 확인하지 못했어요.",
            preview_text=preview_text,
            guide_anchor=spec.get("guide_anchor"),
        )

    known_source_hint = any(
        _contains_variant(preview_text, variants)
        for variants in (
            ("국세청", "홈택스", "국세청 홈택스"),
            ("국민건강보험공단",),
            ("보험료",),
        )
    )
    if known_source_hint:
        return OfficialDataRegistryDecision(
            registry_status=REGISTRY_STATUS_NEEDS_REVIEW,
            supported_document_type=None,
            source_system=None,
            display_name=None,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_error_code="known_source_but_unrecognized",
            detection_reason="공식 자료처럼 보이지만 지원 문서 헤더를 확실히 확인하지 못했어요.",
            preview_text=preview_text,
        )

    return OfficialDataRegistryDecision(
        registry_status=REGISTRY_STATUS_UNSUPPORTED_DOCUMENT,
        supported_document_type=None,
        source_system=None,
        display_name=None,
        parser_version=OFFICIAL_DATA_PARSER_VERSION,
        parse_error_code="unsupported_document_type",
        detection_reason="지원하는 공식 문서 종류를 확인하지 못했어요.",
        preview_text=preview_text,
    )


def resolve_fixture_document(path: str | Path) -> dict[str, Any]:
    envelope = build_envelope_from_path(path)
    decision = identify_official_data_document(envelope)
    parser = get_parser_for_document_type(decision.supported_document_type or "")
    parser_result = parser(envelope) if parser else None
    return {
        "fixture_name": Path(path).name,
        "registry_status": decision.registry_status,
        "document_type": decision.supported_document_type,
        "parse_status": (parser_result.parse_status if parser_result else decision.registry_status),
        "extracted_summary": (parser_result.extracted_key_summary if parser_result else {}),
        "parse_error_code": (parser_result.parse_error_code if parser_result else decision.parse_error_code),
    }
