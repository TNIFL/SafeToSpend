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


def _tabular_registry(rows: list[list[str]] | None) -> RegistryDecision:
    row_texts = _cell_text_rows(rows)
    normalized = " ".join(_normalize(row) for row in row_texts)

    has_hometax = _contains_any(normalized, {"국세청", "홈택스", "hometax"})
    if not has_hometax:
        return RegistryDecision(None, None, "unsupported", "홈택스 공식자료 형식으로 확인되지 않았습니다.")

    is_payment_history = _contains_any(normalized, {"납부내역", "납부내역서", "납부내역조회", "납부내역조회결과"})
    is_withholding = _contains_any(normalized, {"원천징수", "원천징수영수증", "지급명세서"})

    if is_payment_history:
        has_payment_header = _contains_any(normalized, {"납부일", "납부금액", "납부세액", "세목"})
        if has_payment_header:
            return RegistryDecision("hometax_tax_payment_history", "국세청(홈택스)", "identified", "홈택스 납부내역 형식으로 인식했습니다.")
        return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 납부내역으로 보이지만 핵심 표 구조가 불완전합니다.")

    if is_withholding:
        has_withholding_header = _contains_any(normalized, {"지급일", "원천징수세액", "소득구분", "지급액", "총지급액"})
        if has_withholding_header:
            return RegistryDecision("hometax_withholding_statement", "국세청(홈택스)", "identified", "홈택스 원천징수 관련 문서로 인식했습니다.")
        return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 원천징수 관련 문서로 보이지만 핵심 표 구조가 불완전합니다.")

    return RegistryDecision(None, "국세청(홈택스)", "needs_review", "홈택스 공식자료로 보이지만 지원 문서 유형이 아닙니다.")


def _pdf_registry(text: str) -> RegistryDecision:
    normalized = _normalize(text)

    has_nhis = _contains_any(normalized, {"국민건강보험공단", "건강보험공단", "건강보험"})
    if not has_nhis:
        return RegistryDecision(None, None, "unsupported", "현재 지원하는 공식기관 문서로 확인되지 않았습니다.")

    if _contains_any(normalized, {"납부확인서", "납부확인", "보험료납부확인"}):
        has_core_fields = _contains_any(normalized, {"기준일", "확인일", "납부금액", "보험료"})
        if has_core_fields:
            return RegistryDecision("nhis_payment_confirmation", "국민건강보험공단", "identified", "건강보험 납부확인서로 인식했습니다.")
        return RegistryDecision(None, "국민건강보험공단", "needs_review", "건강보험 납부확인서로 보이지만 읽을 수 있는 핵심 문구가 부족합니다.")

    if _contains_any(normalized, {"자격득실", "자격확인", "자격취득", "자격상실"}):
        has_core_fields = _contains_any(normalized, {"기준일", "가입자구분", "자격상태", "취득일", "상실일"})
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
