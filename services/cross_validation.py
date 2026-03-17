from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from domain.models import OfficialDataDocument, ReferenceMaterialItem, Transaction


CROSS_VALIDATION_LABELS = {
    "match": "일치",
    "partial_match": "부분일치",
    "reference_only": "참고용",
    "review_needed": "재확인필요",
    "mismatch": "불일치",
}

MATERIAL_KIND_LABELS = {
    "reference": "참고자료",
    "note_attachment": "추가설명",
}

AUTHORITY_KEYWORDS = {
    "hometax": ("국세청", "홈택스", "국세", "세금", "세무"),
    "nhis": ("국민건강보험공단", "건강보험공단", "국민건강보험", "건강보험", "건보"),
}

DOCUMENT_FAMILIES = {
    "hometax_tax_payment_history": "hometax_payment",
    "nhis_payment_confirmation": "nhis_payment",
    "hometax_withholding_statement": "withholding_reference",
    "nhis_eligibility_status": "eligibility_reference",
}

COMPARABLE_FAMILIES = {"hometax_payment", "nhis_payment"}


@dataclass(frozen=True)
class CrossValidationContext:
    transactions: list[Transaction]
    reference_materials: list[ReferenceMaterialItem]


def normalize_validation_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[_\-\.,:;()\[\]{}]", "", text)
    return text


def normalize_validation_amount(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("원", "").replace(",", "").replace(" ", "")
    text = re.sub(r"[^0-9\-+]", "", text)
    if not text or text in {"-", "+"}:
        return None
    try:
        return int(text)
    except Exception:
        return None


def normalize_validation_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})", text)
    if not match:
        match = re.search(r"(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일", text)
    if not match:
        return None
    try:
        return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    except Exception:
        return None


def _detect_authority_token(text: str | None) -> str:
    normalized = normalize_validation_text(text)
    for token, keywords in AUTHORITY_KEYWORDS.items():
        if any(normalize_validation_text(keyword) in normalized for keyword in keywords):
            return token
    return ""


def _extract_single_date_from_text(text: str | None) -> date | None:
    if not text:
        return None
    found = {
        normalize_validation_date(match.group(0))
        for match in re.finditer(
            r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{4}년\s*\d{1,2}월\s*\d{1,2}일",
            str(text),
        )
    }
    found.discard(None)
    return next(iter(found)) if len(found) == 1 else None


def _extract_single_amount_from_text(text: str | None) -> int | None:
    if not text:
        return None
    found = {
        normalize_validation_amount(match.group(1))
        for match in re.finditer(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})(?:\s*원)?", str(text))
    }
    found.discard(None)
    positives = {value for value in found if value and value > 0}
    return next(iter(positives)) if len(positives) == 1 else None


def _official_summary(document: OfficialDataDocument) -> dict[str, Any]:
    return dict(document.extracted_key_summary_json or {})


def _official_basis(document: OfficialDataDocument) -> dict[str, Any]:
    summary = _official_summary(document)
    family = DOCUMENT_FAMILIES.get(document.document_type or "", "")
    authority_token = _detect_authority_token(document.source_authority)

    if document.document_type == "hometax_tax_payment_history":
        return {
            "family": family,
            "authority_token": authority_token or "hometax",
            "amount_krw": normalize_validation_amount(summary.get("paid_tax_total_krw")),
            "comparison_date": normalize_validation_date(summary.get("latest_payment_date")) or document.reference_date,
            "document_title": "홈택스 납부내역",
        }
    if document.document_type == "nhis_payment_confirmation":
        return {
            "family": family,
            "authority_token": authority_token or "nhis",
            "amount_krw": normalize_validation_amount(summary.get("latest_paid_amount_krw")),
            "comparison_date": document.reference_date,
            "document_title": "건강보험 납부확인서",
        }
    if document.document_type == "hometax_withholding_statement":
        return {
            "family": family,
            "authority_token": authority_token or "hometax",
            "amount_krw": normalize_validation_amount(summary.get("withheld_tax_total_krw")),
            "comparison_date": document.reference_date,
            "document_title": "홈택스 원천징수 관련 문서",
        }
    if document.document_type == "nhis_eligibility_status":
        return {
            "family": family,
            "authority_token": authority_token or "nhis",
            "amount_krw": None,
            "comparison_date": document.reference_date,
            "document_title": "건강보험 자격 관련 문서",
        }
    return {
        "family": "",
        "authority_token": authority_token,
        "amount_krw": None,
        "comparison_date": document.reference_date,
        "document_title": "문서 판별 전",
    }


def _reference_basis(item: ReferenceMaterialItem) -> dict[str, Any]:
    corpus = " ".join(part for part in [item.title, item.note, item.original_filename] if part)
    return {
        "id": int(item.id),
        "material_kind": item.material_kind,
        "material_kind_label": MATERIAL_KIND_LABELS.get(item.material_kind, "참고자료"),
        "title": item.title or item.original_filename,
        "authority_token": _detect_authority_token(corpus),
        "date": _extract_single_date_from_text(corpus),
        "amount_krw": _extract_single_amount_from_text(corpus),
    }


def build_cross_validation_context(*, user_pk: int) -> CrossValidationContext:
    transactions = (
        Transaction.query.filter_by(user_pk=user_pk, direction="out")
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .all()
    )
    reference_materials = (
        ReferenceMaterialItem.query.filter_by(user_pk=user_pk)
        .order_by(ReferenceMaterialItem.created_at.desc(), ReferenceMaterialItem.id.desc())
        .all()
    )
    return CrossValidationContext(transactions=transactions, reference_materials=reference_materials)


def build_official_document_cross_validation(*, document: OfficialDataDocument, context: CrossValidationContext) -> dict[str, Any]:
    basis = _official_basis(document)
    family = basis["family"]
    amount_krw = basis["amount_krw"]
    comparison_date = basis["comparison_date"]
    authority_token = basis["authority_token"]

    result = {
        "status": "reference_only",
        "status_label": CROSS_VALIDATION_LABELS["reference_only"],
        "reason": "이 문서는 현재 교차검증 v1 비교 대상이 아닙니다.",
        "matched_transactions": [],
        "matched_reference_items": [],
        "mismatched_transactions": [],
        "mismatched_reference_items": [],
    }

    if document.parse_status == "needs_review":
        result.update(
            status="review_needed",
            status_label=CROSS_VALIDATION_LABELS["review_needed"],
            reason="공식자료 자체가 검토 필요 상태라 교차검증 결과도 다시 확인이 필요합니다.",
        )
        return result

    if document.parse_status in {"unsupported", "failed"}:
        return result

    if family not in COMPARABLE_FAMILIES:
        return result

    if amount_krw is None or comparison_date is None:
        result.update(
            status="review_needed",
            status_label=CROSS_VALIDATION_LABELS["review_needed"],
            reason="비교에 필요한 금액 또는 날짜가 부족해 재확인이 필요합니다.",
        )
        return result

    strong_tx_matches: list[dict[str, Any]] = []
    partial_tx_matches: list[dict[str, Any]] = []
    mismatch_tx_matches: list[dict[str, Any]] = []

    for tx in context.transactions:
        tx_date = normalize_validation_date(tx.occurred_at)
        if tx_date is None:
            continue
        days_diff = abs((tx_date - comparison_date).days)
        tx_authority = _detect_authority_token(" ".join(part for part in [tx.counterparty, tx.memo] if part))
        authority_match = bool(authority_token and tx_authority == authority_token)
        amount_match = int(tx.amount_krw or 0) == amount_krw

        row = {
            "id": int(tx.id),
            "date": tx_date.isoformat(),
            "amount_krw": int(tx.amount_krw or 0),
            "counterparty": tx.counterparty or "",
            "days_diff": days_diff,
        }
        if amount_match and days_diff <= 3 and authority_match:
            strong_tx_matches.append(row)
        elif amount_match and days_diff <= 7:
            partial_tx_matches.append(row)
        elif authority_match and days_diff <= 7:
            mismatch_tx_matches.append(row)

    strong_ref_matches: list[dict[str, Any]] = []
    partial_ref_matches: list[dict[str, Any]] = []
    mismatch_ref_matches: list[dict[str, Any]] = []

    for item in context.reference_materials:
        ref = _reference_basis(item)
        authority_match = bool(authority_token and ref["authority_token"] == authority_token)
        ref_date = ref["date"]
        ref_amount = ref["amount_krw"]
        date_match = ref_date == comparison_date if ref_date else False
        amount_match = ref_amount == amount_krw if ref_amount is not None else False
        row = {
            "id": ref["id"],
            "title": ref["title"],
            "material_kind_label": ref["material_kind_label"],
        }
        if authority_match and amount_match and date_match:
            strong_ref_matches.append(row)
        elif authority_match and (amount_match or date_match):
            partial_ref_matches.append(row)
        elif authority_match and ref_date == comparison_date and ref_amount is not None and ref_amount != amount_krw:
            mismatch_ref_matches.append(row)

    if strong_tx_matches:
        result.update(
            status="match",
            status_label=CROSS_VALIDATION_LABELS["match"],
            reason=f"거래 {len(strong_tx_matches)}건과 금액·날짜가 일치합니다.",
            matched_transactions=strong_tx_matches,
            matched_reference_items=strong_ref_matches,
            mismatched_transactions=mismatch_tx_matches,
            mismatched_reference_items=mismatch_ref_matches,
        )
        return result

    if partial_tx_matches or strong_ref_matches or partial_ref_matches:
        reasons: list[str] = []
        if partial_tx_matches:
            reasons.append(f"거래 {len(partial_tx_matches)}건과 일부 기준이 일치합니다.")
        if strong_ref_matches or partial_ref_matches:
            ref_total = len(strong_ref_matches) + len(partial_ref_matches)
            reasons.append(f"참고자료 {ref_total}건과 일부 기준이 맞습니다.")
        result.update(
            status="partial_match",
            status_label=CROSS_VALIDATION_LABELS["partial_match"],
            reason=" ".join(reasons) or "일부 기준만 일치합니다.",
            matched_transactions=partial_tx_matches,
            matched_reference_items=strong_ref_matches + partial_ref_matches,
            mismatched_transactions=mismatch_tx_matches,
            mismatched_reference_items=mismatch_ref_matches,
        )
        return result

    if mismatch_tx_matches or mismatch_ref_matches:
        reasons: list[str] = []
        if mismatch_tx_matches:
            reasons.append(f"같은 성격의 거래 {len(mismatch_tx_matches)}건이 있지만 금액 또는 날짜가 다릅니다.")
        if mismatch_ref_matches:
            reasons.append(f"같은 성격의 참고자료 {len(mismatch_ref_matches)}건과 값이 다릅니다.")
        result.update(
            status="mismatch",
            status_label=CROSS_VALIDATION_LABELS["mismatch"],
            reason=" ".join(reasons) or "비교 결과가 맞지 않습니다.",
            mismatched_transactions=mismatch_tx_matches,
            mismatched_reference_items=mismatch_ref_matches,
        )
        return result

    result.update(
        status="review_needed",
        status_label=CROSS_VALIDATION_LABELS["review_needed"],
        reason="비교 가능한 거래나 참고자료가 부족해 재확인이 필요합니다.",
    )
    return result
