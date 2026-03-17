from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core.time import utcnow
from services.official_data_extractors import OfficialDataFileEnvelope, extract_matrix, extract_preview_text

OFFICIAL_DATA_PARSER_VERSION = "official_data_parser_v1"
PARSE_STATUS_PARSED = "parsed"
PARSE_STATUS_NEEDS_REVIEW = "needs_review"
PARSE_STATUS_UNSUPPORTED = "unsupported"
PARSE_STATUS_FAILED = "failed"


@dataclass(slots=True)
class OfficialDataParseResult:
    source_system: str
    document_type: str
    display_name: str
    parser_name: str
    parser_version: str
    parse_status: str
    parse_error_code: str | None = None
    parse_error_detail: str | None = None
    extracted_payload: dict[str, Any] | None = None
    extracted_key_summary: dict[str, Any] | None = None
    document_issued_at: datetime | None = None
    document_period_start: date | None = None
    document_period_end: date | None = None
    verified_reference_date: date | None = None
    structure_validation_status: str = "not_applicable"


DATE_FRAGMENT_PATTERN = r"[0-9]{4}(?:[./-][0-9]{1,2}(?:[./-][0-9]{1,2})?|년\s*[0-9]{1,2}\s*월\s*[0-9]{1,2}\s*일)"
MAX_HEADER_SCAN_ROWS = 4

DOCUMENT_TITLE_VARIANTS: dict[str, tuple[str, ...]] = {
    "hometax_withholding_statement": ("원천징수 이행상황 신고서", "원천징수이행상황신고서"),
    "hometax_business_card_usage": ("사업용 신용카드 사용내역", "사업용신용카드사용내역"),
    "hometax_tax_payment_history": (
        "세금 납부내역 조회",
        "세금납부내역조회",
        "세금 납부내역서",
        "세금납부내역서",
        "납부 내역",
        "납부내역",
    ),
    "nhis_payment_confirmation": ("보험료 납부확인서", "보험료납부확인서"),
    "nhis_eligibility_status": ("자격득실확인서", "자격득실 확인서"),
}

DOCUMENT_ISSUER_VARIANTS: dict[str, tuple[str, ...]] = {
    "hometax_withholding_statement": ("국세청 홈택스", "국세청", "홈택스"),
    "hometax_business_card_usage": ("국세청 홈택스", "국세청", "홈택스"),
    "hometax_tax_payment_history": ("국세청 홈택스", "국세청", "홈택스"),
    "nhis_payment_confirmation": ("국민건강보험공단",),
    "nhis_eligibility_status": ("국민건강보험공단",),
}

TABULAR_HEADER_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "hometax_withholding_statement": {
        "문서명": ("문서명", "문서 명", "자료명"),
        "발급기관": ("발급기관", "발급 기관", "기관명"),
        "기준일": ("기준일", "조회일", "기준 일"),
        "귀속기간시작": ("귀속기간시작", "귀속기간 시작", "귀속 시작일"),
        "귀속기간종료": ("귀속기간종료", "귀속기간 종료", "귀속 종료일"),
        "총 원천징수세액": ("총 원천징수세액", "총원천징수세액", "원천징수세액 합계", "원천징수세액합계"),
        "지급처 식별키": ("지급처 식별키", "지급처식별키", "지급처 참조", "지급처 코드"),
        "지급건수": ("지급건수", "지급 건수", "건수"),
    },
    "hometax_business_card_usage": {
        "문서명": ("문서명", "문서 명", "자료명"),
        "발급기관": ("발급기관", "발급 기관", "기관명"),
        "기준일": ("기준일", "조회일", "기준 일"),
        "사용기간시작": ("사용기간시작", "사용기간 시작", "사용 시작일"),
        "사용기간종료": ("사용기간종료", "사용기간 종료", "사용 종료일"),
        "총 사용금액": ("총 사용금액", "총사용금액", "사용금액 합계", "총 사용 금액"),
        "사업자 식별키": ("사업자 식별키", "사업자식별키", "사업자 참조", "사업자 코드"),
        "승인건수": ("승인건수", "승인 건수", "건수"),
        "카드 구분": ("카드 구분", "카드구분", "카드 유형"),
    },
    "hometax_tax_payment_history": {
        "문서명": ("문서명", "문서 명", "자료명"),
        "발급기관": ("발급기관", "발급 기관", "기관명"),
        "조회일": ("조회일", "기준일", "조회 일", "기준 일"),
        "세목": ("세목", "세목명", "세목 요약"),
        "납부일": ("납부일", "최근 납부일", "납부 일"),
        "납부세액 합계": ("납부세액 합계", "납부세액합계", "납부금액 합계", "납부세액", "납부 금액 합계"),
        "귀속기간시작": ("귀속기간시작", "귀속기간 시작", "대상기간시작", "대상기간 시작"),
        "귀속기간종료": ("귀속기간종료", "귀속기간 종료", "대상기간종료", "대상기간 종료"),
        "납부건수": ("납부건수", "납부 건수", "건수"),
    },
}


@dataclass(slots=True)
class TabularRecordLookup:
    record: dict[str, str]
    observed_headers: list[str]
    missing_required_fields: list[str]
    matched_required_count: int


def _parse_date(raw: str | None) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    korean_match = re.fullmatch(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if korean_match:
        year, month, day = korean_match.groups()
        try:
            return date(int(year), int(month), int(day))
        except Exception:
            return None
    text = text.replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.date()
        except Exception:
            continue
    return None


def _parse_datetime(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    korean_date = _parse_date(text)
    if korean_date:
        return datetime.combine(korean_date, datetime.min.time())
    text = text.replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    d = _parse_date(text)
    return datetime.combine(d, datetime.min.time()) if d else None


def _parse_int_krw(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"[^0-9-]", "", text)
    if cleaned in {"", "-"}:
        return None
    try:
        return int(cleaned)
    except Exception:
        return None


def _mask_identifier(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"***{text[-4:]}"


def _normalize_fragment(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s\-_:/|().,:;{}\[\]<>·]+", "", text)


def _header_matches_alias(header: str, alias: str) -> bool:
    header_norm = _normalize_fragment(header)
    alias_norm = _normalize_fragment(alias)
    if not header_norm or not alias_norm:
        return False
    return header_norm == alias_norm or header_norm.startswith(alias_norm)


def _contains_variant(text: str, variants: tuple[str, ...]) -> bool:
    normalized = _normalize_fragment(text)
    return any(_normalize_fragment(variant) in normalized for variant in variants if str(variant or "").strip())


def _flex_label_pattern(label: str) -> str:
    chars = [re.escape(ch) for ch in str(label or "").strip() if not ch.isspace()]
    return r"\s*".join(chars)


def _find_labeled_value(text: str, labels: tuple[str, ...], value_pattern: str) -> str | None:
    label_pattern = "|".join(_flex_label_pattern(label) for label in labels if str(label or "").strip())
    if not label_pattern:
        return None
    match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*({value_pattern})", text)
    if not match:
        return None
    return str(match.group(1) or "").strip()


def _find_labeled_date(text: str, labels: tuple[str, ...]) -> date | None:
    return _parse_date(_find_labeled_value(text, labels, DATE_FRAGMENT_PATTERN))


def _find_labeled_datetime(text: str, labels: tuple[str, ...]) -> datetime | None:
    return _parse_datetime(_find_labeled_value(text, labels, DATE_FRAGMENT_PATTERN))


def _find_labeled_amount(text: str, labels: tuple[str, ...]) -> int | None:
    return _parse_int_krw(_find_labeled_value(text, labels, r"[0-9][0-9,\s]*원?"))


def _find_labeled_text(text: str, labels: tuple[str, ...]) -> str:
    return str(_find_labeled_value(text, labels, r"[^\n]+") or "").strip()


def _find_labeled_period(text: str, labels: tuple[str, ...]) -> tuple[date | None, date | None]:
    label_pattern = "|".join(_flex_label_pattern(label) for label in labels if str(label or "").strip())
    if not label_pattern:
        return (None, None)
    match = re.search(
        rf"(?:{label_pattern})\s*[:：]?\s*({DATE_FRAGMENT_PATTERN})\s*[~\-]\s*({DATE_FRAGMENT_PATTERN})",
        text,
    )
    if not match:
        return (None, None)
    return (_parse_date(match.group(1)), _parse_date(match.group(2)))


def _find_next_data_row(matrix: list[list[str]], start_index: int) -> list[str]:
    for row in matrix[start_index:]:
        cleaned = [str(cell or "").strip() for cell in row]
        if sum(1 for cell in cleaned if cell) >= 2:
            return cleaned
    return []


def _lookup_tabular_record(
    matrix: list[list[str]],
    *,
    document_type: str,
    required_fields: tuple[str, ...],
) -> TabularRecordLookup:
    alias_map = TABULAR_HEADER_ALIASES[document_type]
    best_headers: list[str] = []
    best_positions: dict[str, int] = {}
    best_score = 0

    for row_index, row in enumerate(matrix[:MAX_HEADER_SCAN_ROWS]):
        headers = [str(cell or "").strip() for cell in row]
        positions: dict[str, int] = {}
        for field in required_fields:
            aliases = alias_map.get(field, (field,))
            for idx, header in enumerate(headers):
                if any(_header_matches_alias(header, alias) for alias in aliases):
                    positions[field] = idx
                    break
        if len(positions) > best_score:
            best_score = len(positions)
            best_headers = headers
            best_positions = positions
        if len(positions) == len(required_fields):
            data_row = _find_next_data_row(matrix, row_index + 1)
            record = {
                field: (str(data_row[idx] or "").strip() if idx < len(data_row) else "")
                for field, idx in positions.items()
            }
            optional_fields = [field for field in alias_map.keys() if field not in required_fields]
            for field in optional_fields:
                for idx, header in enumerate(headers):
                    if any(_header_matches_alias(header, alias) for alias in alias_map.get(field, ())):
                        record[field] = str(data_row[idx] or "").strip() if idx < len(data_row) else ""
                        break
            missing_required = [field for field in required_fields if not record.get(field)]
            return TabularRecordLookup(
                record=record,
                observed_headers=headers,
                missing_required_fields=missing_required,
                matched_required_count=len(required_fields),
            )

    return TabularRecordLookup(
        record={},
        observed_headers=best_headers,
        missing_required_fields=[field for field in required_fields if field not in best_positions],
        matched_required_count=best_score,
    )


def _result_summary(*, issuer: str, document_name: str, verified_reference_date: date | None, period_start: date | None, period_end: date | None, total_amount_krw: int | None, primary_key_label: str, primary_key_value: str) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "document_name": document_name,
        "verified_reference_date": verified_reference_date.isoformat() if verified_reference_date else None,
        "document_period_start": period_start.isoformat() if period_start else None,
        "document_period_end": period_end.isoformat() if period_end else None,
        "total_amount_krw": total_amount_krw,
        "primary_key_label": primary_key_label.replace("식별키", "식별 참조"),
        "primary_key_value": _mask_identifier(primary_key_value),
    }


def _needs_review(*, source_system: str, document_type: str, display_name: str, parser_name: str, error_code: str, detail: str, partial_payload: dict[str, Any] | None = None) -> OfficialDataParseResult:
    return OfficialDataParseResult(
        source_system=source_system,
        document_type=document_type,
        display_name=display_name,
        parser_name=parser_name,
        parser_version=OFFICIAL_DATA_PARSER_VERSION,
        parse_status=PARSE_STATUS_NEEDS_REVIEW,
        parse_error_code=error_code,
        parse_error_detail=detail,
        extracted_payload=partial_payload or {},
        extracted_key_summary={},
        structure_validation_status="partial",
    )


def _failed(*, source_system: str, document_type: str, display_name: str, parser_name: str, error_code: str, detail: str) -> OfficialDataParseResult:
    return OfficialDataParseResult(
        source_system=source_system,
        document_type=document_type,
        display_name=display_name,
        parser_name=parser_name,
        parser_version=OFFICIAL_DATA_PARSER_VERSION,
        parse_status=PARSE_STATUS_FAILED,
        parse_error_code=error_code,
        parse_error_detail=detail,
        extracted_payload={},
        extracted_key_summary={},
        structure_validation_status="failed",
    )


def _tabular_single_record(matrix: list[list[str]]) -> dict[str, str]:
    if len(matrix) < 2:
        return {}
    headers = [str(x or "").strip() for x in matrix[0]]
    values = [str(x or "").strip() for x in matrix[1]]
    pairs: dict[str, str] = {}
    for idx, header in enumerate(headers):
        if not header:
            continue
        pairs[header] = values[idx] if idx < len(values) else ""
    return pairs


def parse_hometax_withholding_statement(envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    parser_name = "parse_hometax_withholding_statement"
    try:
        matrix = extract_matrix(envelope)
        required = ("문서명", "발급기관", "기준일", "귀속기간시작", "귀속기간종료", "총 원천징수세액", "지급처 식별키")
        lookup = _lookup_tabular_record(
            matrix,
            document_type="hometax_withholding_statement",
            required_fields=required,
        )
        record = lookup.record
        missing = [key for key in required if key in lookup.missing_required_fields or not str(record.get(key) or "").strip()]
        if missing:
            return _needs_review(
                source_system="hometax",
                document_type="hometax_withholding_statement",
                display_name="이미 빠진 세금/원천징수 자료",
                parser_name=parser_name,
                error_code="missing_required_fields",
                detail=f"필수 헤더 또는 값이 부족해요: {', '.join(missing)}",
                partial_payload={"observed_headers": lookup.observed_headers, "missing_required_fields": missing},
            )
        verified_reference_date = _parse_date(record.get("기준일"))
        period_start = _parse_date(record.get("귀속기간시작"))
        period_end = _parse_date(record.get("귀속기간종료"))
        total_tax_krw = _parse_int_krw(record.get("총 원천징수세액"))
        if not verified_reference_date or not period_start or not period_end or total_tax_krw is None:
            return _needs_review(
                source_system="hometax",
                document_type="hometax_withholding_statement",
                display_name="이미 빠진 세금/원천징수 자료",
                parser_name=parser_name,
                error_code="invalid_core_values",
                detail="기준일/기간/세액을 확실히 읽지 못했어요.",
                partial_payload={"observed_headers": list(record.keys()), "core_value_parse_failed": True},
            )
        payload = {
            "issuer_name": record.get("발급기관"),
            "document_name": record.get("문서명"),
            "verified_reference_date": verified_reference_date.isoformat(),
            "document_period_start": period_start.isoformat(),
            "document_period_end": period_end.isoformat(),
            "total_withheld_tax_krw": total_tax_krw,
            "statement_count": _parse_int_krw(record.get("지급건수")) or 0,
            "payor_key": record.get("지급처 식별키") or "",
            "fixture_source": envelope.filename,
        }
        return OfficialDataParseResult(
            source_system="hometax",
            document_type="hometax_withholding_statement",
            display_name="이미 빠진 세금/원천징수 자료",
            parser_name=parser_name,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_status=PARSE_STATUS_PARSED,
            extracted_payload=payload,
            extracted_key_summary=_result_summary(
                issuer=str(record.get("발급기관") or "국세청/홈택스"),
                document_name=str(record.get("문서명") or "원천징수 자료"),
                verified_reference_date=verified_reference_date,
                period_start=period_start,
                period_end=period_end,
                total_amount_krw=total_tax_krw,
                primary_key_label="지급처 식별키",
                primary_key_value=str(record.get("지급처 식별키") or ""),
            ),
            document_issued_at=_parse_datetime(record.get("기준일")),
            document_period_start=period_start,
            document_period_end=period_end,
            verified_reference_date=verified_reference_date,
            structure_validation_status="passed",
        )
    except Exception as exc:
        return _failed(
            source_system="hometax",
            document_type="hometax_withholding_statement",
            display_name="이미 빠진 세금/원천징수 자료",
            parser_name=parser_name,
            error_code="parser_exception",
            detail=str(exc),
        )


def parse_hometax_business_card_usage(envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    parser_name = "parse_hometax_business_card_usage"
    try:
        matrix = extract_matrix(envelope)
        required = ("문서명", "발급기관", "기준일", "사용기간시작", "사용기간종료", "총 사용금액", "사업자 식별키")
        lookup = _lookup_tabular_record(
            matrix,
            document_type="hometax_business_card_usage",
            required_fields=required,
        )
        record = lookup.record
        missing = [key for key in required if key in lookup.missing_required_fields or not str(record.get(key) or "").strip()]
        if missing:
            return _needs_review(
                source_system="hometax",
                document_type="hometax_business_card_usage",
                display_name="사업용 카드 사용내역",
                parser_name=parser_name,
                error_code="missing_required_fields",
                detail=f"필수 헤더 또는 값이 부족해요: {', '.join(missing)}",
                partial_payload={"observed_headers": lookup.observed_headers, "missing_required_fields": missing},
            )
        verified_reference_date = _parse_date(record.get("기준일"))
        period_start = _parse_date(record.get("사용기간시작"))
        period_end = _parse_date(record.get("사용기간종료"))
        total_amount_krw = _parse_int_krw(record.get("총 사용금액"))
        if not verified_reference_date or not period_start or not period_end or total_amount_krw is None:
            return _needs_review(
                source_system="hometax",
                document_type="hometax_business_card_usage",
                display_name="사업용 카드 사용내역",
                parser_name=parser_name,
                error_code="invalid_core_values",
                detail="기준일/기간/금액을 확실히 읽지 못했어요.",
                partial_payload={"observed_headers": list(record.keys()), "core_value_parse_failed": True},
            )
        payload = {
            "issuer_name": record.get("발급기관"),
            "document_name": record.get("문서명"),
            "verified_reference_date": verified_reference_date.isoformat(),
            "document_period_start": period_start.isoformat(),
            "document_period_end": period_end.isoformat(),
            "total_card_usage_krw": total_amount_krw,
            "approval_count": _parse_int_krw(record.get("승인건수")) or 0,
            "card_type": record.get("카드 구분") or "",
            "business_key": record.get("사업자 식별키") or "",
            "fixture_source": envelope.filename,
        }
        return OfficialDataParseResult(
            source_system="hometax",
            document_type="hometax_business_card_usage",
            display_name="사업용 카드 사용내역",
            parser_name=parser_name,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_status=PARSE_STATUS_PARSED,
            extracted_payload=payload,
            extracted_key_summary=_result_summary(
                issuer=str(record.get("발급기관") or "국세청/홈택스"),
                document_name=str(record.get("문서명") or "사업용 카드 사용내역"),
                verified_reference_date=verified_reference_date,
                period_start=period_start,
                period_end=period_end,
                total_amount_krw=total_amount_krw,
                primary_key_label="사업자 식별키",
                primary_key_value=str(record.get("사업자 식별키") or ""),
            ),
            document_issued_at=_parse_datetime(record.get("기준일")),
            document_period_start=period_start,
            document_period_end=period_end,
            verified_reference_date=verified_reference_date,
            structure_validation_status="passed",
        )
    except Exception as exc:
        return _failed(
            source_system="hometax",
            document_type="hometax_business_card_usage",
            display_name="사업용 카드 사용내역",
            parser_name=parser_name,
            error_code="parser_exception",
            detail=str(exc),
        )


def parse_hometax_tax_payment_history(envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    parser_name = "parse_hometax_tax_payment_history"
    try:
        record: dict[str, str]
        if envelope.ext in {".csv", ".xlsx"}:
            matrix = extract_matrix(envelope)
            required = ("문서명", "발급기관", "조회일", "세목", "납부일", "납부세액 합계", "귀속기간시작", "귀속기간종료")
            lookup = _lookup_tabular_record(
                matrix,
                document_type="hometax_tax_payment_history",
                required_fields=required,
            )
            record = lookup.record
            missing = [key for key in required if key in lookup.missing_required_fields or not str(record.get(key) or "").strip()]
            if missing:
                return _needs_review(
                    source_system="hometax",
                    document_type="hometax_tax_payment_history",
                    display_name="홈택스 납부내역",
                    parser_name=parser_name,
                    error_code="missing_required_fields",
                    detail=f"필수 헤더 또는 값이 부족해요: {', '.join(missing)}",
                    partial_payload={"observed_headers": lookup.observed_headers, "missing_required_fields": missing},
                )
            verified_reference_date = _parse_date(record.get("조회일") or record.get("기준일"))
            payment_date = _parse_date(record.get("납부일") or record.get("최근 납부일"))
            period_start = _parse_date(record.get("귀속기간시작"))
            period_end = _parse_date(record.get("귀속기간종료"))
            paid_total_krw = _parse_int_krw(record.get("납부세액 합계") or record.get("납부세액"))
            tax_type_summary = str(record.get("세목") or record.get("세목 요약") or "").strip()
            if not verified_reference_date or not payment_date or paid_total_krw is None or not tax_type_summary:
                return _needs_review(
                    source_system="hometax",
                    document_type="hometax_tax_payment_history",
                    display_name="홈택스 납부내역",
                    parser_name=parser_name,
                    error_code="invalid_core_values",
                    detail="기준일/납부일/납부세액/세목을 확실히 읽지 못했어요.",
                    partial_payload={"observed_headers": list(record.keys()), "core_value_parse_failed": True},
                )
            payload = {
                "issuer_name": record.get("발급기관"),
                "document_name": record.get("문서명"),
                "verified_reference_date": verified_reference_date.isoformat(),
                "latest_payment_date": payment_date.isoformat(),
                "document_period_start": period_start.isoformat() if period_start else None,
                "document_period_end": period_end.isoformat() if period_end else None,
                "paid_tax_total_krw": paid_total_krw,
                "tax_type_summary": tax_type_summary,
                "payment_entry_count": _parse_int_krw(record.get("납부건수")) or 0,
            }
            return OfficialDataParseResult(
                source_system="hometax",
                document_type="hometax_tax_payment_history",
                display_name="홈택스 납부내역",
                parser_name=parser_name,
                parser_version=OFFICIAL_DATA_PARSER_VERSION,
                parse_status=PARSE_STATUS_PARSED,
                extracted_payload=payload,
                extracted_key_summary=_result_summary(
                    issuer=str(record.get("발급기관") or "국세청/홈택스"),
                    document_name=str(record.get("문서명") or "홈택스 납부내역"),
                    verified_reference_date=verified_reference_date,
                    period_start=period_start,
                    period_end=period_end,
                    total_amount_krw=paid_total_krw,
                    primary_key_label="세목 요약",
                    primary_key_value=tax_type_summary,
                ),
                document_issued_at=_parse_datetime(record.get("조회일") or record.get("기준일")),
                document_period_start=period_start,
                document_period_end=period_end,
                verified_reference_date=verified_reference_date,
                structure_validation_status="passed",
            )

        text = extract_preview_text(envelope)
        if not _contains_variant(text, DOCUMENT_TITLE_VARIANTS["hometax_tax_payment_history"]) or not _contains_variant(
            text,
            DOCUMENT_ISSUER_VARIANTS["hometax_tax_payment_history"],
        ):
            return _needs_review(
                source_system="hometax",
                document_type="hometax_tax_payment_history",
                display_name="홈택스 납부내역",
                parser_name=parser_name,
                error_code="document_header_mismatch",
                detail="문서명이나 발급기관을 확실히 읽지 못했어요.",
                partial_payload={
                    "document_name_marker_found": bool(_contains_variant(text, DOCUMENT_TITLE_VARIANTS["hometax_tax_payment_history"])),
                    "issuer_marker_found": bool(_contains_variant(text, DOCUMENT_ISSUER_VARIANTS["hometax_tax_payment_history"])),
                },
            )
        verified_reference_date = _find_labeled_date(text, ("조회일", "기준일"))
        payment_date = _find_labeled_date(text, ("납부일", "최근 납부일"))
        paid_total_krw = _find_labeled_amount(text, ("납부세액 합계", "납부세액", "납부금액 합계"))
        tax_type_summary = _find_labeled_text(text, ("세목", "세목 요약", "세목명"))
        period_start, period_end = _find_labeled_period(text, ("귀속기간", "대상기간"))
        if not verified_reference_date or not payment_date or paid_total_krw is None or not tax_type_summary:
            return _needs_review(
                source_system="hometax",
                document_type="hometax_tax_payment_history",
                display_name="홈택스 납부내역",
                parser_name=parser_name,
                error_code="invalid_core_values",
                detail="기준일/납부일/납부세액/세목을 확실히 읽지 못했어요.",
                partial_payload={
                    "reference_found": bool(verified_reference_date),
                    "payment_date_found": bool(payment_date),
                    "amount_found": bool(paid_total_krw is not None),
                    "tax_type_found": bool(tax_type_summary),
                },
            )
        payload = {
            "issuer_name": "국세청 홈택스",
            "document_name": "세금 납부내역 조회",
            "verified_reference_date": verified_reference_date.isoformat(),
            "latest_payment_date": payment_date.isoformat(),
            "document_period_start": period_start.isoformat() if period_start else None,
            "document_period_end": period_end.isoformat() if period_end else None,
            "paid_tax_total_krw": paid_total_krw,
            "tax_type_summary": tax_type_summary,
            "payment_entry_count": 1,
        }
        return OfficialDataParseResult(
            source_system="hometax",
            document_type="hometax_tax_payment_history",
            display_name="홈택스 납부내역",
            parser_name=parser_name,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_status=PARSE_STATUS_PARSED,
            extracted_payload=payload,
            extracted_key_summary=_result_summary(
                issuer="국세청 홈택스",
                document_name="세금 납부내역 조회",
                verified_reference_date=verified_reference_date,
                period_start=period_start,
                period_end=period_end,
                total_amount_krw=paid_total_krw,
                primary_key_label="세목 요약",
                primary_key_value=tax_type_summary,
            ),
            document_issued_at=_parse_datetime(verified_reference_date.isoformat()),
            document_period_start=period_start,
            document_period_end=period_end,
            verified_reference_date=verified_reference_date,
            structure_validation_status="passed",
        )
    except Exception as exc:
        return _failed(
            source_system="hometax",
            document_type="hometax_tax_payment_history",
            display_name="홈택스 납부내역",
            parser_name=parser_name,
            error_code="parser_exception",
            detail=str(exc),
        )


def parse_nhis_payment_confirmation(envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    parser_name = "parse_nhis_payment_confirmation"
    try:
        text = extract_preview_text(envelope)
        if not _contains_variant(text, DOCUMENT_TITLE_VARIANTS["nhis_payment_confirmation"]) or not _contains_variant(
            text,
            DOCUMENT_ISSUER_VARIANTS["nhis_payment_confirmation"],
        ):
            return _needs_review(
                source_system="nhis",
                document_type="nhis_payment_confirmation",
                display_name="건보료 납부확인서",
                parser_name=parser_name,
                error_code="document_header_mismatch",
                detail="문서명이나 발급기관을 확실히 읽지 못했어요.",
                partial_payload={
                    "document_name_marker_found": bool(_contains_variant(text, DOCUMENT_TITLE_VARIANTS["nhis_payment_confirmation"])),
                    "issuer_marker_found": bool(_contains_variant(text, DOCUMENT_ISSUER_VARIANTS["nhis_payment_confirmation"])),
                },
            )
        issued_at = _find_labeled_datetime(text, ("발급일", "기준일"))
        period_start, period_end = _find_labeled_period(text, ("납부대상기간", "납부 대상 기간", "대상기간"))
        total_amount_krw = _find_labeled_amount(text, ("납부보험료 합계", "보험료 합계", "최근 공식 납부금액"))
        insured_key = _find_labeled_text(text, ("가입자 식별키", "가입자식별키", "가입자 참조"))
        member_type = _find_labeled_text(text, ("가입자 구분", "가입자구분", "가입자 유형"))
        if not issued_at or not period_start or not period_end or total_amount_krw is None or not insured_key:
            return _needs_review(
                source_system="nhis",
                document_type="nhis_payment_confirmation",
                display_name="건보료 납부확인서",
                parser_name=parser_name,
                error_code="invalid_core_values",
                detail="기준일/기간/납부액/가입자 식별키를 확실히 읽지 못했어요.",
                partial_payload={
                    "issued_at_found": bool(issued_at),
                    "period_found": bool(period_start and period_end),
                    "total_amount_found": bool(total_amount_krw is not None),
                    "identifier_found": bool(insured_key),
                },
            )
        payload = {
            "issuer_name": "국민건강보험공단",
            "document_name": "보험료 납부확인서",
            "verified_reference_date": issued_at.date().isoformat(),
            "document_period_start": period_start.isoformat(),
            "document_period_end": period_end.isoformat(),
            "total_paid_amount_krw": total_amount_krw,
            "insured_key": insured_key,
            "member_type": member_type,
            "fixture_source": envelope.filename,
        }
        return OfficialDataParseResult(
            source_system="nhis",
            document_type="nhis_payment_confirmation",
            display_name="건보료 납부확인서",
            parser_name=parser_name,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_status=PARSE_STATUS_PARSED,
            extracted_payload=payload,
            extracted_key_summary=_result_summary(
                issuer="국민건강보험공단",
                document_name="보험료 납부확인서",
                verified_reference_date=issued_at.date(),
                period_start=period_start,
                period_end=period_end,
                total_amount_krw=total_amount_krw,
                primary_key_label="가입자 식별키",
                primary_key_value=insured_key,
            ),
            document_issued_at=issued_at,
            document_period_start=period_start,
            document_period_end=period_end,
            verified_reference_date=issued_at.date(),
            structure_validation_status="passed",
        )
    except Exception as exc:
        return _failed(
            source_system="nhis",
            document_type="nhis_payment_confirmation",
            display_name="건보료 납부확인서",
            parser_name=parser_name,
            error_code="parser_exception",
            detail=str(exc),
        )


def parse_nhis_eligibility_status(envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    parser_name = "parse_nhis_eligibility_status"
    try:
        text = extract_preview_text(envelope)
        if not _contains_variant(text, DOCUMENT_TITLE_VARIANTS["nhis_eligibility_status"]) or not _contains_variant(
            text,
            DOCUMENT_ISSUER_VARIANTS["nhis_eligibility_status"],
        ):
            return _needs_review(
                source_system="nhis",
                document_type="nhis_eligibility_status",
                display_name="NHIS 자격 상태 자료",
                parser_name=parser_name,
                error_code="document_header_mismatch",
                detail="문서명이나 발급기관을 확실히 읽지 못했어요.",
                partial_payload={
                    "document_name_marker_found": bool(_contains_variant(text, DOCUMENT_TITLE_VARIANTS["nhis_eligibility_status"])),
                    "issuer_marker_found": bool(_contains_variant(text, DOCUMENT_ISSUER_VARIANTS["nhis_eligibility_status"])),
                },
            )
        verified_reference_date = _find_labeled_date(text, ("기준일", "발급일"))
        subscriber_type = _find_labeled_text(text, ("가입자 유형", "가입자 구분", "가입자구분"))
        eligibility_status = _find_labeled_text(text, ("자격 상태", "자격 현황", "자격상태", "자격현황"))
        eligibility_start_date = _find_labeled_date(text, ("취득일", "자격취득일"))
        eligibility_end_date = _find_labeled_date(text, ("상실일", "자격상실일"))
        latest_status_change_date = _find_labeled_date(text, ("최근 변동일", "변동일"))
        if not verified_reference_date or not subscriber_type or not eligibility_status:
            return _needs_review(
                source_system="nhis",
                document_type="nhis_eligibility_status",
                display_name="NHIS 자격 상태 자료",
                parser_name=parser_name,
                error_code="invalid_core_values",
                detail="기준일/가입자 유형/자격 상태를 확실히 읽지 못했어요.",
                partial_payload={
                    "reference_found": bool(verified_reference_date),
                    "subscriber_type_found": bool(subscriber_type),
                    "eligibility_status_found": bool(eligibility_status),
                },
            )
        payload = {
            "issuer_name": "국민건강보험공단",
            "document_name": "자격득실확인서",
            "verified_reference_date": verified_reference_date.isoformat(),
            "subscriber_type": subscriber_type,
            "eligibility_status": eligibility_status,
            "eligibility_start_date": eligibility_start_date.isoformat() if eligibility_start_date else None,
            "eligibility_end_date": eligibility_end_date.isoformat() if eligibility_end_date else None,
            "latest_status_change_date": latest_status_change_date.isoformat() if latest_status_change_date else None,
        }
        return OfficialDataParseResult(
            source_system="nhis",
            document_type="nhis_eligibility_status",
            display_name="NHIS 자격 상태 자료",
            parser_name=parser_name,
            parser_version=OFFICIAL_DATA_PARSER_VERSION,
            parse_status=PARSE_STATUS_PARSED,
            extracted_payload=payload,
            extracted_key_summary=_result_summary(
                issuer="국민건강보험공단",
                document_name="자격득실확인서",
                verified_reference_date=verified_reference_date,
                period_start=eligibility_start_date,
                period_end=eligibility_end_date,
                total_amount_krw=None,
                primary_key_label="자격 상태",
                primary_key_value=eligibility_status,
            ),
            document_issued_at=_parse_datetime(verified_reference_date.isoformat()),
            document_period_start=eligibility_start_date,
            document_period_end=eligibility_end_date,
            verified_reference_date=verified_reference_date,
            structure_validation_status="passed",
        )
    except Exception as exc:
        return _failed(
            source_system="nhis",
            document_type="nhis_eligibility_status",
            display_name="NHIS 자격 상태 자료",
            parser_name=parser_name,
            error_code="parser_exception",
            detail=str(exc),
        )


def parse_fixture_for_registry(document_type: str, envelope: OfficialDataFileEnvelope) -> OfficialDataParseResult:
    if document_type == "hometax_withholding_statement":
        return parse_hometax_withholding_statement(envelope)
    if document_type == "hometax_business_card_usage":
        return parse_hometax_business_card_usage(envelope)
    if document_type == "hometax_tax_payment_history":
        return parse_hometax_tax_payment_history(envelope)
    if document_type == "nhis_payment_confirmation":
        return parse_nhis_payment_confirmation(envelope)
    if document_type == "nhis_eligibility_status":
        return parse_nhis_eligibility_status(envelope)
    return OfficialDataParseResult(
        source_system="hometax",
        document_type=document_type,
        display_name=document_type,
        parser_name="parse_fixture_for_registry",
        parser_version=OFFICIAL_DATA_PARSER_VERSION,
        parse_status=PARSE_STATUS_UNSUPPORTED,
        parse_error_code="parser_not_registered",
        parse_error_detail="지원 parser가 아직 없어요.",
        extracted_payload={},
        extracted_key_summary={},
        structure_validation_status="not_applicable",
    )


def write_parser_smoke_report(*, fixture_paths: list[Path], resolver: Any, output_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fixture_path in fixture_paths:
        decision = resolver(fixture_path)
        rows.append(decision)
    report = {
        "generated_at": utcnow().isoformat(timespec="seconds"),
        "row_count": len(rows),
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
