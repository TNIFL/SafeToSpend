from __future__ import annotations

import re
from dataclasses import dataclass


SUPPORTED_DOCUMENT_TYPES = {
    "hometax_withholding_statement",
    "hometax_tax_payment_history",
    "nhis_payment_confirmation",
    "nhis_eligibility_status",
}


DOCUMENT_TYPE_LABELS = {
    "hometax_withholding_statement": "홈택스 원천징수 관련 문서",
    "hometax_tax_payment_history": "홈택스 납부내역",
    "nhis_payment_confirmation": "건강보험 납부확인서",
    "nhis_eligibility_status": "건강보험 자격 관련 문서",
}


_HOMETAX_TITLE_VARIANTS = {
    "hometax_tax_payment_history": {
        "납부내역",
        "납부내역서",
        "납부내역조회",
        "납부내역조회결과",
        "세금납부내역",
        "세금납부내역서",
        "세금납부내역조회",
    },
    "hometax_withholding_statement": {
        "원천징수",
        "원천징수영수증",
        "원천징수이행상황신고서",
        "지급명세서",
    },
}

_HOMETAX_HEADER_ALIASES = {
    "hometax_tax_payment_history": {
        "payment_date": ("납부일", "최근 납부일", "납부일자"),
        "paid_tax": ("납부세액", "납부금액", "납부세액 합계", "납부금액 합계"),
        "tax_type": ("세목", "세목명", "세금종류"),
    },
    "hometax_withholding_statement_line": {
        "payment_date": ("지급일", "지급일자", "지급일시"),
        "withheld_tax": ("원천징수세액", "원천징수 세액", "징수세액"),
    },
    "hometax_withholding_statement_summary": {
        "reference_date": ("조회일", "기준일", "기준 일"),
        "period_start": ("귀속기간 시작", "귀속기간시작", "귀속 시작일"),
        "period_end": ("귀속기간 종료", "귀속기간종료", "귀속 종료일"),
        "withheld_tax": ("원천징수세액 합계", "원천징수세액합계", "원천징수 세액 합계"),
    },
}

_NHIS_TITLE_VARIANTS = {
    "nhis_payment_confirmation": {
        "납부확인서",
        "납부확인",
        "보험료납부확인",
        "보험료납부확인서",
        "건강보험납부확인서",
    },
    "nhis_eligibility_status": {
        "자격득실",
        "자격득실확인서",
        "자격확인",
        "자격취득",
        "자격상실",
    },
}

_NHIS_CORE_TOKENS = {
    "nhis_payment_confirmation": {"기준일", "확인일", "발급일", "납부금액", "보험료", "납부대상기간"},
    "nhis_eligibility_status": {"기준일", "가입자구분", "가입자유형", "자격상태", "취득일", "상실일"},
}


@dataclass(frozen=True)
class RegistryDecision:
    document_type: str | None
    source_authority: str | None
    registry_status: str
    reason: str


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[_\-\.,:;()\[\]{}]", "", text)
    return text


def _cell_text_rows(rows: list[list[str]] | None, limit: int = 6) -> list[str]:
    if not rows:
        return []
    return [" ".join((cell or "").strip() for cell in row if (cell or "").strip()) for row in rows[:limit]]


def _contains_any(text: str, patterns: set[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _header_matches_alias(cell: str, alias: str) -> bool:
    normalized_cell = _normalize(cell)
    normalized_alias = _normalize(alias)
    if not normalized_cell or not normalized_alias:
        return False
    return normalized_cell == normalized_alias or normalized_cell.startswith(normalized_alias)


def _best_header_alias_score(rows: list[list[str]] | None, aliases: dict[str, tuple[str, ...]], limit: int = 4) -> int:
    if not rows:
        return 0
    best_score = 0
    for row in rows[:limit]:
        score = 0
        for names in aliases.values():
            if any(_header_matches_alias(cell or "", alias) for cell in row for alias in names):
                score += 1
        best_score = max(best_score, score)
    return best_score


def _tabular_registry(rows: list[list[str]] | None) -> RegistryDecision:
    row_texts = _cell_text_rows(rows)
    normalized = " ".join(_normalize(row) for row in row_texts)

    has_hometax = _contains_any(normalized, {"국세청", "홈택스", "hometax"})
    if not has_hometax:
        return RegistryDecision(None, None, "unsupported", "홈택스 공식자료 형식으로 확인되지 않았습니다.")

    payment_title = _contains_any(normalized, _HOMETAX_TITLE_VARIANTS["hometax_tax_payment_history"])
    payment_score = _best_header_alias_score(rows, _HOMETAX_HEADER_ALIASES["hometax_tax_payment_history"])
    if payment_score >= 3 and (payment_title or _contains_any(normalized, {"납부", "세목"})):
        return RegistryDecision("hometax_tax_payment_history", "국세청(홈택스)", "identified", "홈택스 납부내역 형식으로 인식했습니다.")
    if payment_title or payment_score > 0:
        return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 납부내역으로 보이지만 핵심 표 구조가 불완전합니다.")

    withholding_title = _contains_any(normalized, _HOMETAX_TITLE_VARIANTS["hometax_withholding_statement"])
    withholding_line_score = _best_header_alias_score(rows, _HOMETAX_HEADER_ALIASES["hometax_withholding_statement_line"])
    withholding_summary_score = _best_header_alias_score(rows, _HOMETAX_HEADER_ALIASES["hometax_withholding_statement_summary"])
    if (
        withholding_title and (withholding_line_score >= 2 or withholding_summary_score >= 4)
    ) or withholding_summary_score >= 4:
        return RegistryDecision("hometax_withholding_statement", "국세청(홈택스)", "identified", "홈택스 원천징수 관련 문서로 인식했습니다.")
    if withholding_title or withholding_line_score > 0 or withholding_summary_score > 0:
        return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 원천징수 관련 문서로 보이지만 핵심 표 구조가 불완전합니다.")

    return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 공식자료로 보이지만 지원 문서 유형이 아닙니다.")


def _pdf_registry(text: str) -> RegistryDecision:
    normalized = _normalize(text)

    has_nhis = _contains_any(normalized, {"국민건강보험공단", "건강보험공단", "건강보험"})
    if not has_nhis:
        return RegistryDecision(None, None, "unsupported", "현재 지원하는 공식기관 문서로 확인되지 않았습니다.")

    if _contains_any(normalized, _NHIS_TITLE_VARIANTS["nhis_payment_confirmation"]):
        has_core_fields = _contains_any(normalized, _NHIS_CORE_TOKENS["nhis_payment_confirmation"])
        if has_core_fields:
            return RegistryDecision("nhis_payment_confirmation", "국민건강보험공단", "identified", "건강보험 납부확인서로 인식했습니다.")
        return RegistryDecision(None, "국민건강보험공단", "needs_review", "건강보험 납부확인서로 보이지만 읽을 수 있는 핵심 문구가 부족합니다.")

    if _contains_any(normalized, _NHIS_TITLE_VARIANTS["nhis_eligibility_status"]):
        has_core_fields = _contains_any(normalized, _NHIS_CORE_TOKENS["nhis_eligibility_status"])
        if has_core_fields:
            return RegistryDecision("nhis_eligibility_status", "국민건강보험공단", "identified", "건강보험 자격 관련 문서로 인식했습니다.")
        return RegistryDecision(None, "국민건강보험공단", "needs_review", "건강보험 자격 관련 문서로 보이지만 읽을 수 있는 핵심 문구가 부족합니다.")

    return RegistryDecision(None, "국민건강보험공단", "needs_review", "건강보험 공식자료로 보이지만 현재 지원 문서 유형이 아닙니다.")


def identify_official_data_document(
    *,
    extension: str,
    rows: list[list[str]] | None = None,
    extracted_text: str = "",
) -> RegistryDecision:
    ext = (extension or "").lower()
    if ext in {".csv", ".xlsx"}:
        return _tabular_registry(rows)
    if ext == ".pdf":
        return _pdf_registry(extracted_text)
    return RegistryDecision(None, None, "unsupported", "지원하지 않는 공식자료 형식입니다.")
