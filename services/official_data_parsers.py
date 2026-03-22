from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


PARSER_VERSION = "official-data-v1"


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[_\-\.,:;()\[\]{}]", "", text)
    return text


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    patterns = [
        r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})",
        r"(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        except Exception:
            return None
    return None


def _parse_int_amount(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    neg = False
    if text.startswith("(") and text.endswith(")"):
        neg = True
        text = text[1:-1]
    text = text.replace("원", "").replace(",", "").replace(" ", "")
    text = re.sub(r"[^0-9.\-+]", "", text)
    if not text or text in {"-", "+", ".", "-.", "+."}:
        return None
    try:
        value = int(round(float(text)))
    except Exception:
        return None
    return -abs(value) if neg else value


def _flex_label_pattern(label: str) -> str:
    parts = [re.escape(ch) for ch in str(label or "").strip() if not ch.isspace()]
    return r"\s*".join(parts)


def _find_labeled_value(text: str, labels: tuple[str, ...], value_pattern: str) -> str | None:
    label_pattern = "|".join(_flex_label_pattern(label) for label in labels if str(label or "").strip())
    if not label_pattern:
        return None
    match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*({value_pattern})", text)
    if not match:
        return None
    return str(match.group(1) or "").strip()


def _find_labeled_date(text: str, labels: tuple[str, ...]) -> date | None:
    value = _find_labeled_value(
        text,
        labels,
        r"[0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일",
    )
    return _parse_date(value)


def _find_labeled_amount(text: str, labels: tuple[str, ...]) -> int | None:
    value = _find_labeled_value(text, labels, r"[0-9][0-9, ]*(?:원)?")
    return _parse_int_amount(value)


def _find_labeled_text(text: str, labels: tuple[str, ...], stop_labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(_flex_label_pattern(label) for label in labels if str(label or "").strip())
    stop_pattern = "|".join(_flex_label_pattern(label) for label in stop_labels if str(label or "").strip())
    match = re.search(
        rf"(?:{label_pattern})\s*[:：]?\s*([가-힣A-Za-z0-9()/_\- ]+?)(?=\s*(?:{stop_pattern}|$))",
        text,
    )
    return match.group(1).strip() if match else ""


def _row_text(rows: list[list[str]], index: int) -> str:
    if index < 0 or index >= len(rows):
        return ""
    return " ".join((cell or "").strip() for cell in rows[index] if (cell or "").strip())


def _detect_withholding_material_kind(rows: list[list[str]]) -> str:
    normalized = " ".join(_normalize(_row_text(rows, idx)) for idx in range(min(len(rows), 6)))
    if "원천징수이행상황신고서" in normalized:
        return "원천징수이행상황신고서 계열"
    if "지급명세서" in normalized:
        return "지급명세서 계열"
    if "원천징수영수증" in normalized:
        return "원천징수영수증 계열"
    return "원천징수 관련 자료"


def _find_header_index(rows: list[list[str]], aliases: dict[str, tuple[str, ...]], required_fields: tuple[str, ...]) -> tuple[int | None, dict[str, int]]:
    for idx, row in enumerate(rows[:6]):
        header_map: dict[str, int] = {}
        for col_idx, cell in enumerate(row):
            normalized = _normalize(cell)
            if not normalized:
                continue
            for field, names in aliases.items():
                if any(normalized == _normalize(name) or normalized.startswith(_normalize(name)) for name in names):
                    header_map.setdefault(field, col_idx)
        if all(field in header_map for field in required_fields):
            return idx, header_map
    return None, {}


def _row_value(row: list[str], header_map: dict[str, int], field: str) -> str:
    index = header_map.get(field)
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _iter_data_rows(rows: list[list[str]], header_idx: int) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows[header_idx + 1 :]:
        if not any((cell or "").strip() for cell in row):
            continue
        row_text = _normalize(" ".join((cell or "") for cell in row))
        if row_text in {"합계", "총계", "소계"}:
            continue
        if row_text.startswith(("합계", "총계", "소계")) and sum(1 for cell in row if (cell or "").strip()) <= 2:
            continue
        out.append(row)
    return out


def _summary_payload(*, document_title: str, reference_date: date | None, items: list[tuple[str, str]], values: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    summary = {
        "document_title": document_title,
        "reference_date": reference_date.isoformat() if reference_date else None,
        "display_summary": [{"label": label, "value": value} for label, value in items if value],
    }
    summary.update(values)
    if reason:
        summary["status_reason"] = reason
    return summary


def parse_hometax_withholding_statement(rows: list[list[str]]) -> dict[str, Any]:
    material_kind = _detect_withholding_material_kind(rows)
    line_item_aliases = {
        "payment_date": ("지급일", "지급일자", "지급일시"),
        "withheld_tax": ("원천징수세액", "원천징수 세액", "징수세액"),
        "gross_pay": ("지급액", "총지급액", "지급총액"),
        "income_type": ("소득구분", "소득 구분"),
    }
    summary_aliases = {
        "reference_date": ("조회일", "기준일", "기준 일"),
        "period_start": ("귀속기간 시작", "귀속기간시작", "귀속 시작일"),
        "period_end": ("귀속기간 종료", "귀속기간종료", "귀속 종료일"),
        "withheld_tax": ("원천징수세액 합계", "원천징수세액합계", "원천징수 세액 합계"),
        "payer_key": ("지급처 코드", "지급처코드", "지급처 식별키", "지급처식별키"),
    }
    header_idx, header_map = _find_header_index(rows, line_item_aliases, ("payment_date", "withheld_tax"))
    if header_idx is not None:
        data_rows = _iter_data_rows(rows, header_idx)
        withheld_total = 0
        latest_date: date | None = None
        gross_total = 0
        income_types: set[str] = set()

        for row in data_rows:
            payment_date = _parse_date(_row_value(row, header_map, "payment_date"))
            withheld = _parse_int_amount(_row_value(row, header_map, "withheld_tax"))
            if payment_date is None or withheld is None:
                continue
            latest_date = max(filter(None, [latest_date, payment_date])) if latest_date else payment_date
            withheld_total += withheld
            gross_pay = _parse_int_amount(_row_value(row, header_map, "gross_pay"))
            if gross_pay is not None:
                gross_total += gross_pay
            value = _row_value(row, header_map, "income_type")
            if value:
                income_types.add(value)

        if latest_date is not None and withheld_total > 0:
            return {
                "parse_status": "parsed",
                "reference_date": latest_date,
                "structure_validation_status": "passed",
                "summary": _summary_payload(
                    document_title="홈택스 원천징수 관련 문서",
                    reference_date=latest_date,
                    items=[
                        ("자료 구분", material_kind),
                        ("기준일", latest_date.isoformat()),
                        ("원천징수세액 합계", f"{withheld_total:,}원"),
                        ("총지급액 합계", f"{gross_total:,}원" if gross_total else ""),
                        ("소득구분", ", ".join(sorted(income_types)) if income_types else ""),
                    ],
                    values={
                        "withholding_material_kind": material_kind,
                        "withheld_tax_total_krw": withheld_total,
                        "gross_pay_total_krw": gross_total or None,
                        "income_type_summary": ", ".join(sorted(income_types)) if income_types else None,
                    },
                ),
            }

    summary_header_idx, summary_header_map = _find_header_index(
        rows,
        summary_aliases,
        ("reference_date", "period_start", "period_end", "withheld_tax"),
    )
    if summary_header_idx is not None:
        for row in _iter_data_rows(rows, summary_header_idx):
            reference_date = _parse_date(_row_value(row, summary_header_map, "reference_date"))
            period_start = _parse_date(_row_value(row, summary_header_map, "period_start"))
            period_end = _parse_date(_row_value(row, summary_header_map, "period_end"))
            withheld_total = _parse_int_amount(_row_value(row, summary_header_map, "withheld_tax"))
            payer_key = _row_value(row, summary_header_map, "payer_key")

            if reference_date is None or period_start is None or period_end is None or withheld_total is None or withheld_total <= 0:
                continue

            return {
                "parse_status": "parsed",
                "reference_date": reference_date,
                "structure_validation_status": "passed",
                "summary": _summary_payload(
                    document_title="홈택스 원천징수 관련 문서",
                    reference_date=reference_date,
                    items=[
                        ("자료 구분", material_kind),
                        ("기준일", reference_date.isoformat()),
                        ("귀속기간", f"{period_start.isoformat()} ~ {period_end.isoformat()}"),
                        ("원천징수세액 합계", f"{withheld_total:,}원"),
                        ("지급처 참조", payer_key),
                    ],
                    values={
                        "withholding_material_kind": material_kind,
                        "withheld_tax_total_krw": withheld_total,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "payer_reference": payer_key or None,
                    },
                ),
            }

    reason = "원천징수 문서의 지급일/원천징수세액 표 또는 요약 구조를 찾지 못했습니다."
    return {
        "parse_status": "needs_review",
        "reference_date": None,
        "structure_validation_status": "needs_review",
        "summary": _summary_payload(
            document_title="홈택스 원천징수 관련 문서",
            reference_date=None,
            items=[],
            values={},
            reason=reason,
        ),
    }


def parse_hometax_tax_payment_history(rows: list[list[str]]) -> dict[str, Any]:
    summary_aliases = {
        "reference_date": ("조회일", "기준일", "기준 일"),
        "payment_date": ("납부일", "최근 납부일", "납부일자"),
        "paid_tax": ("납부세액", "납부금액", "납부세액 합계", "납부금액 합계"),
        "tax_type": ("세목", "세목명", "세금종류"),
        "period_start": ("귀속기간 시작", "귀속기간시작", "대상기간 시작", "대상기간시작"),
        "period_end": ("귀속기간 종료", "귀속기간종료", "대상기간 종료", "대상기간종료"),
        "entry_count": ("건수", "납부건수", "납부 건수"),
    }
    summary_header_idx, summary_header_map = _find_header_index(
        rows,
        summary_aliases,
        ("reference_date", "payment_date", "paid_tax", "tax_type"),
    )
    if summary_header_idx is not None:
        for row in _iter_data_rows(rows, summary_header_idx):
            reference_date = _parse_date(_row_value(row, summary_header_map, "reference_date"))
            latest_payment_date = _parse_date(_row_value(row, summary_header_map, "payment_date"))
            paid_total = _parse_int_amount(_row_value(row, summary_header_map, "paid_tax"))
            tax_type = _row_value(row, summary_header_map, "tax_type")
            period_start = _parse_date(_row_value(row, summary_header_map, "period_start"))
            period_end = _parse_date(_row_value(row, summary_header_map, "period_end"))
            entry_count = _parse_int_amount(_row_value(row, summary_header_map, "entry_count"))

            if reference_date is None or latest_payment_date is None or paid_total is None or paid_total <= 0 or not tax_type:
                continue

            period_summary = ""
            if period_start and period_end:
                period_summary = f"{period_start.isoformat()} ~ {period_end.isoformat()}"
            return {
                "parse_status": "parsed",
                "reference_date": reference_date,
                "structure_validation_status": "passed",
                "summary": _summary_payload(
                    document_title="홈택스 납부내역",
                    reference_date=reference_date,
                    items=[
                        ("기준일", reference_date.isoformat()),
                        ("최근 납부일", latest_payment_date.isoformat()),
                        ("납부세액 합계", f"{paid_total:,}원"),
                        ("세목", tax_type),
                        ("기간", period_summary),
                    ],
                    values={
                        "paid_tax_total_krw": paid_total,
                        "latest_payment_date": latest_payment_date.isoformat(),
                        "tax_type_summary": tax_type,
                        "period_summary": period_summary or None,
                        "payment_entry_count": entry_count,
                    },
                ),
            }

    aliases = {
        "payment_date": ("납부일", "최근납부일", "납부일자"),
        "paid_tax": ("납부세액", "납부금액", "납부세액합계", "납부금액합계"),
        "tax_type": ("세목", "세목명", "세금종류"),
        "period": ("귀속기간", "기간", "납부대상기간"),
    }
    header_idx, header_map = _find_header_index(rows, aliases, ("payment_date", "paid_tax", "tax_type"))
    reference_date = None
    for i in range(min(4, len(rows))):
        reference_date = _find_labeled_date(_row_text(rows, i), ("조회일", "기준일", "기준 일", "작성일"))
        if reference_date:
            break

    if header_idx is None:
        reason = "납부일/납부세액/세목 표를 찾지 못했습니다."
        return {
            "parse_status": "needs_review",
            "reference_date": reference_date,
            "structure_validation_status": "needs_review",
            "summary": _summary_payload(
                document_title="홈택스 납부내역",
                reference_date=reference_date,
                items=[("기준일", reference_date.isoformat() if reference_date else "")],
                values={},
                reason=reason,
            ),
        }

    data_rows = _iter_data_rows(rows, header_idx)
    latest_payment_date: date | None = None
    paid_total = 0
    tax_types: set[str] = set()
    periods: set[str] = set()

    for row in data_rows:
        payment_date = _parse_date(_row_value(row, header_map, "payment_date"))
        paid_tax = _parse_int_amount(_row_value(row, header_map, "paid_tax"))
        tax_type = _row_value(row, header_map, "tax_type")
        if _normalize(tax_type) in {"합계", "총계", "소계"}:
            continue
        if payment_date is None or paid_tax is None or not tax_type:
            continue
        latest_payment_date = max(filter(None, [latest_payment_date, payment_date])) if latest_payment_date else payment_date
        paid_total += paid_tax
        tax_types.add(tax_type)
        period = _row_value(row, header_map, "period")
        if period:
            periods.add(period)

    if latest_payment_date is None or paid_total <= 0 or not tax_types:
        reason = "납부일/납부세액/세목을 충분히 읽지 못해 검토가 필요합니다."
        return {
            "parse_status": "needs_review",
            "reference_date": reference_date or latest_payment_date,
            "structure_validation_status": "needs_review",
            "summary": _summary_payload(
                document_title="홈택스 납부내역",
                reference_date=reference_date or latest_payment_date,
                items=[
                    ("기준일", (reference_date or latest_payment_date).isoformat() if (reference_date or latest_payment_date) else ""),
                    ("납부세액 합계", f"{paid_total:,}원" if paid_total else ""),
                ],
                values={"paid_tax_total_krw": paid_total or None},
                reason=reason,
            ),
        }

    final_reference_date = reference_date or latest_payment_date
    return {
        "parse_status": "parsed",
        "reference_date": final_reference_date,
        "structure_validation_status": "passed",
        "summary": _summary_payload(
            document_title="홈택스 납부내역",
            reference_date=final_reference_date,
            items=[
                ("기준일", final_reference_date.isoformat() if final_reference_date else ""),
                ("최근 납부일", latest_payment_date.isoformat()),
                ("납부세액 합계", f"{paid_total:,}원"),
                ("세목", ", ".join(sorted(tax_types))),
                ("기간", ", ".join(sorted(periods)) if periods else ""),
            ],
            values={
                "paid_tax_total_krw": paid_total,
                "latest_payment_date": latest_payment_date.isoformat(),
                "tax_type_summary": ", ".join(sorted(tax_types)),
                "period_summary": ", ".join(sorted(periods)) if periods else None,
            },
        ),
    }


def parse_nhis_payment_confirmation(extracted_text: str) -> dict[str, Any]:
    reference_date = _find_labeled_date(extracted_text, ("기준일", "확인일", "발급일"))
    latest_paid_amount = _find_labeled_amount(
        extracted_text,
        ("납부금액", "보험료", "최근납부금액", "보험료 합계", "납부보험료 합계"),
    )
    subscriber_type = _find_labeled_text(
        extracted_text,
        ("가입자구분", "가입자 유형", "가입자유형"),
        ("납부금액", "보험료", "최근납부금액", "보험료 합계", "납부보험료 합계", "기준일", "확인일", "발급일"),
    )
    insured_reference = _find_labeled_text(
        extracted_text,
        ("가입자식별키", "가입자 식별키", "가입자 참조"),
        ("가입자구분", "가입자 유형", "가입자유형", "납부금액", "보험료", "최근납부금액", "보험료 합계"),
    )
    period_start = _find_labeled_date(extracted_text, ("납부대상기간 시작", "납부 대상 기간 시작", "대상기간 시작"))
    period_end = _find_labeled_date(extracted_text, ("납부대상기간 종료", "납부 대상 기간 종료", "대상기간 종료"))
    if period_start is None or period_end is None:
        period_match = re.search(
            rf"(?:{_flex_label_pattern('납부대상기간')}|{_flex_label_pattern('납부 대상 기간')}|{_flex_label_pattern('대상기간')})\s*[:：]?\s*"
            r"([0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)"
            r"\s*[~\-]\s*"
            r"([0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)",
            extracted_text,
        )
        if period_match:
            period_start = period_start or _parse_date(period_match.group(1))
            period_end = period_end or _parse_date(period_match.group(2))

    if reference_date is None or latest_paid_amount is None:
        reason = "건강보험 납부확인서에서 기준일 또는 납부금액을 충분히 읽지 못했습니다."
        return {
            "parse_status": "needs_review",
            "reference_date": reference_date,
            "structure_validation_status": "needs_review",
            "summary": _summary_payload(
                document_title="건강보험 납부확인서",
                reference_date=reference_date,
                items=[
                    ("기준일", reference_date.isoformat() if reference_date else ""),
                    ("최근 납부금액", f"{latest_paid_amount:,}원" if latest_paid_amount is not None else ""),
                ],
                values={"latest_paid_amount_krw": latest_paid_amount},
                reason=reason,
            ),
        }

    return {
        "parse_status": "parsed",
        "reference_date": reference_date,
        "structure_validation_status": "passed",
        "summary": _summary_payload(
            document_title="건강보험 납부확인서",
            reference_date=reference_date,
            items=[
                ("기준일", reference_date.isoformat()),
                ("최근 납부금액", f"{latest_paid_amount:,}원"),
                ("가입자구분", subscriber_type),
                ("대상기간", f"{period_start.isoformat()} ~ {period_end.isoformat()}" if period_start and period_end else ""),
                ("가입자 참조", insured_reference),
            ],
            values={
                "latest_paid_amount_krw": latest_paid_amount,
                "subscriber_type": subscriber_type or None,
                "insured_reference": insured_reference or None,
                "period_start": period_start.isoformat() if period_start else None,
                "period_end": period_end.isoformat() if period_end else None,
            },
        ),
    }


def parse_nhis_eligibility_status(extracted_text: str) -> dict[str, Any]:
    reference_date = _find_labeled_date(extracted_text, ("기준일", "발급일", "확인일"))
    eligibility_start = _find_labeled_date(extracted_text, ("취득일", "자격취득일"))
    eligibility_end = _find_labeled_date(extracted_text, ("상실일", "자격상실일"))
    subscriber_type = _find_labeled_text(
        extracted_text,
        ("가입자구분", "가입자 유형", "가입자유형"),
        ("자격상태", "자격 상태", "자격현황", "자격 현황", "취득일", "자격취득일", "상실일", "자격상실일", "변동일", "최근 변동일"),
    )
    eligibility_status = _find_labeled_text(
        extracted_text,
        ("자격상태", "자격 상태", "자격현황", "자격 현황", "자격"),
        ("취득일", "자격취득일", "상실일", "자격상실일", "변동일", "최근 변동일"),
    )
    latest_status_change = _find_labeled_date(extracted_text, ("변동일", "최근 변동일"))

    if reference_date is None or (not subscriber_type and not eligibility_status):
        reason = "건강보험 자격 문서의 기준일 또는 자격 정보를 충분히 읽지 못했습니다."
        return {
            "parse_status": "needs_review",
            "reference_date": reference_date,
            "structure_validation_status": "needs_review",
            "summary": _summary_payload(
                document_title="건강보험 자격 관련 문서",
                reference_date=reference_date,
                items=[
                    ("기준일", reference_date.isoformat() if reference_date else ""),
                    ("가입자구분", subscriber_type),
                    ("자격상태", eligibility_status),
                ],
                values={
                    "subscriber_type": subscriber_type or None,
                    "eligibility_status": eligibility_status or None,
                },
                reason=reason,
            ),
        }

    return {
        "parse_status": "parsed",
        "reference_date": reference_date,
        "structure_validation_status": "passed",
        "summary": _summary_payload(
            document_title="건강보험 자격 관련 문서",
            reference_date=reference_date,
            items=[
                ("기준일", reference_date.isoformat()),
                ("가입자구분", subscriber_type),
                ("자격상태", eligibility_status),
                ("취득일", eligibility_start.isoformat() if eligibility_start else ""),
                ("상실일", eligibility_end.isoformat() if eligibility_end else ""),
                ("변동일", latest_status_change.isoformat() if latest_status_change else ""),
            ],
            values={
                "subscriber_type": subscriber_type or None,
                "eligibility_status": eligibility_status or None,
                "eligibility_start_date": eligibility_start.isoformat() if eligibility_start else None,
                "eligibility_end_date": eligibility_end.isoformat() if eligibility_end else None,
                "latest_status_change_date": latest_status_change.isoformat() if latest_status_change else None,
            },
        ),
    }


def parse_official_data_document(*, document_type: str, rows: list[list[str]] | None = None, extracted_text: str = "") -> dict[str, Any]:
    if document_type == "hometax_withholding_statement":
        return parse_hometax_withholding_statement(rows or [])
    if document_type == "hometax_tax_payment_history":
        return parse_hometax_tax_payment_history(rows or [])
    if document_type == "nhis_payment_confirmation":
        return parse_nhis_payment_confirmation(extracted_text)
    if document_type == "nhis_eligibility_status":
        return parse_nhis_eligibility_status(extracted_text)
    return {
        "parse_status": "unsupported",
        "reference_date": None,
        "structure_validation_status": "unsupported",
        "summary": _summary_payload(
            document_title="미지원 공식자료",
            reference_date=None,
            items=[],
            values={},
            reason="현재 지원하지 않는 공식자료 문서 유형입니다.",
        ),
    }
