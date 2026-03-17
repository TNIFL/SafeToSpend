from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func

from core.extensions import db
from core.time import utcnow
from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    ImportJob,
    IncomeLabel,
    SafeToSpendSettings,
    TaxBufferLedger,
    Transaction,
    User,
)
from services.evidence_vault import resolve_file_path


KST = ZoneInfo("Asia/Seoul")
HEADER_FILL = PatternFill("solid", fgColor="E8EEF8")
HEADER_FONT = Font(bold=True)
TOP_ALIGN = Alignment(vertical="top")
PACKAGE_VERSION = "거래+증빙 패키지 v1"


@dataclass(frozen=True)
class PackageStats:
    month_key: str
    period_start_kst: str
    period_end_kst: str
    generated_at_kst: str
    tx_total: int
    tx_in_count: int
    tx_out_count: int
    sum_in_total: int
    sum_out_total: int
    income_included_total: int
    income_excluded_non_income_total: int
    income_unknown_count: int
    expense_business_total: int
    expense_personal_total: int
    expense_mixed_total: int
    expense_unknown_total: int
    evidence_missing_required_count: int
    evidence_missing_required_amount: int
    evidence_missing_maybe_count: int
    evidence_missing_maybe_amount: int
    evidence_attached_count: int
    review_needed_count: int
    tax_rate: float
    tax_buffer_total: int
    tax_buffer_target: int
    tax_buffer_shortage: int


@dataclass(frozen=True)
class PackageSnapshot:
    root_name: str
    download_name: str
    display_name: str
    stats: PackageStats
    transactions: list[dict[str, Any]]
    evidences: list[dict[str, Any]]
    review_items: list[dict[str, Any]]
    evidence_missing_items: list[dict[str, Any]]
    review_trade_items: list[dict[str, Any]]
    included_source_labels: list[str]


def _month_range_kst_naive(month_key: str) -> tuple[datetime, datetime]:
    y, m = month_key.split("-")
    y = int(y)
    m = int(m)
    start = datetime(y, m, 1, 0, 0, 0)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0)
    return start, end


def _get_settings(user_pk: int) -> SafeToSpendSettings | None:
    return SafeToSpendSettings.query.get(user_pk)


def _tax_rate(settings: SafeToSpendSettings | None) -> float:
    rate = float(getattr(settings, "default_tax_rate", 0.15) or 0.15)
    if rate > 1:
        rate = rate / 100.0
    return max(0.0, min(rate, 0.95))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _krw(value: int) -> str:
    return f"{int(value or 0):,}원"


def _to_kst(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _fmt_kst(dt: datetime | None, fmt: str) -> str:
    converted = _to_kst(dt)
    return converted.strftime(fmt) if converted else ""


def _safe_package_label(value: str | None, fallback: str) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    return text or fallback


def _safe_attachment_name(filename: str | None, fallback: str) -> str:
    text = (filename or "").strip() or fallback
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = text.replace("..", ".")
    return text[:160] or fallback


def _source_labels(source: str | None) -> tuple[str, str]:
    raw = (source or "").strip().lower()
    if raw == "manual":
        return "수동입력", "없음"
    if raw == "csv":
        return "수동업로드", "없음"
    if raw == "popbill":
        return "자동연동", "은행연동"
    if raw:
        return "기타", "없음"
    return "기타", "없음"


def _classification_labels(tx: dict[str, Any]) -> tuple[str, str, str, bool, str, str]:
    direction = tx.get("direction")
    reasons: list[str] = []

    if direction == "in":
        income_status = tx.get("income_label_status") or "unknown"
        if income_status == "income":
            classification = "수입"
            business = "해당없음"
            calculation = "예"
        elif income_status == "non_income":
            classification = "수입 아님"
            business = "해당없음"
            calculation = "아니오"
        else:
            classification = "미확정"
            business = "해당없음"
            calculation = "보류"
            reasons.append("수입 분류가 아직 확정되지 않았습니다")
    else:
        expense_status = tx.get("expense_label_status") or "unknown"
        evidence_requirement = tx.get("evidence_requirement") or ""
        evidence_status = tx.get("evidence_status") or ""

        if expense_status == "business":
            classification = "업무지출"
            business = "예"
            calculation = "예"
        elif expense_status == "personal":
            classification = "개인지출"
            business = "아니오"
            calculation = "아니오"
        elif expense_status == "mixed":
            classification = "혼합지출"
            business = "혼합"
            calculation = "보류"
            reasons.append("업무/개인 지출 구분이 혼합 상태입니다")
        else:
            classification = "미확정"
            business = "미확정"
            calculation = "보류"
            reasons.append("지출 분류가 아직 확정되지 않았습니다")

        if evidence_status == "missing" and evidence_requirement == "required":
            calculation = "보류"
            reasons.append("필수 증빙이 아직 첨부되지 않았습니다")
        elif evidence_status == "missing" and evidence_requirement == "maybe":
            calculation = "보류"
            reasons.append("증빙 확인이 필요한 거래입니다")

    recheck_required = bool(reasons)
    recheck_reason = " / ".join(reasons)

    if recheck_required:
        trust = "재확인필요"
    elif tx.get("source") == "manual":
        trust = "참고용"
    else:
        trust = "반영됨"

    return classification, business, calculation, recheck_required, recheck_reason, trust


def _evidence_status_label(requirement: str | None, status: str | None) -> str:
    requirement = (requirement or "").strip()
    status = (status or "").strip()
    if status == "attached":
        return "첨부됨"
    if requirement == "not_needed" or status == "not_needed":
        return "불필요"
    if status == "missing" and requirement == "required":
        return "필수 누락"
    if status == "missing" and requirement == "maybe":
        return "확인 필요"
    return "상태 확인 필요"


def _evidence_type_label(mime_type: str | None, filename: str | None) -> str:
    mime = (mime_type or "").strip().lower()
    ext = Path(filename or "").suffix.lower()
    if mime.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}:
        return "이미지 증빙"
    if mime == "application/pdf" or ext == ".pdf":
        return "PDF 증빙"
    return "증빙파일"


def _build_review_items(transactions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    combined: list[dict[str, Any]] = []
    evidence_missing: list[dict[str, Any]] = []
    review_trades: list[dict[str, Any]] = []

    item_no = 1
    for tx in transactions:
        reasons: list[str] = []
        needed: list[str] = []
        current: list[str] = []
        related_material = "거래내역"
        item_type = "거래검토"
        priority = "보통"

        if tx.get("direction") == "out":
            if tx.get("evidence_status") == "missing" and tx.get("evidence_requirement") == "required":
                reasons.append("필수 증빙이 누락되었습니다")
                needed.append("대표 증빙을 첨부하거나 불필요 여부를 다시 판단해 주세요")
                current.append("필수 누락")
                related_material = "증빙자료"
                item_type = "증빙누락"
                priority = "높음"
                evidence_missing.append(
                    {
                        "거래번호": tx["tx_id"],
                        "거래일시": tx["occurred_at_kst"],
                        "거래처": tx["counterparty"],
                        "금액": tx["amount_krw"],
                        "증빙상태": "필수 누락",
                        "필요한확인내용": needed[-1],
                        "우선순위": priority,
                    }
                )
            elif tx.get("evidence_status") == "missing" and tx.get("evidence_requirement") == "maybe":
                reasons.append("증빙 확인이 필요한 거래입니다")
                needed.append("업무 관련이면 증빙을 첨부하고, 아니면 불필요로 표시해 주세요")
                current.append("확인 필요")
                related_material = "증빙자료"
                item_type = "증빙검토"
                priority = "보통"
                evidence_missing.append(
                    {
                        "거래번호": tx["tx_id"],
                        "거래일시": tx["occurred_at_kst"],
                        "거래처": tx["counterparty"],
                        "금액": tx["amount_krw"],
                        "증빙상태": "확인 필요",
                        "필요한확인내용": needed[-1],
                        "우선순위": priority,
                    }
                )

            if tx.get("expense_label_status") in {"unknown", "mixed", ""}:
                reasons.append("지출 분류가 확정되지 않았습니다")
                needed.append("업무/개인/혼합 중 하나로 확정해 주세요")
                current.append("분류 미확정")
                review_trades.append(
                    {
                        "거래번호": tx["tx_id"],
                        "거래일시": tx["occurred_at_kst"],
                        "자료출처": tx["source_label"],
                        "거래처": tx["counterparty"],
                        "금액": tx["amount_krw"],
                        "현재상태": tx["classification_result_label"],
                        "재확인사유": "업무/개인 판단이 확정되지 않았습니다",
                        "필요한확인내용": "업무/개인/혼합 중 하나로 확정해 주세요",
                    }
                )
        else:
            if tx.get("income_label_status") in {"unknown", ""}:
                reasons.append("수입 분류가 확정되지 않았습니다")
                needed.append("수입인지 수입 아님인지 확인해 주세요")
                current.append("분류 미확정")
                review_trades.append(
                    {
                        "거래번호": tx["tx_id"],
                        "거래일시": tx["occurred_at_kst"],
                        "자료출처": tx["source_label"],
                        "거래처": tx["counterparty"],
                        "금액": tx["amount_krw"],
                        "현재상태": tx["classification_result_label"],
                        "재확인사유": "수입/비수입 판단이 확정되지 않았습니다",
                        "필요한확인내용": "수입인지 수입 아님인지 확인해 주세요",
                    }
                )

        if not reasons:
            continue

        combined.append(
            {
                "항목번호": item_no,
                "항목유형": item_type,
                "관련자료구분": related_material,
                "관련번호": tx["tx_id"],
                "요약설명": " / ".join(reasons),
                "현재상태": " / ".join(current) or tx["trust_label"],
                "필요한확인내용": " / ".join(needed),
                "우선순위": priority,
                "메모": tx.get("memo") or "",
            }
        )
        item_no += 1

    return combined, evidence_missing, review_trades


def _collect_package_snapshot(user_pk: int, month_key: str) -> PackageSnapshot:
    month_key = (month_key or "").strip()
    if not month_key or len(month_key) != 7 or month_key[4] != "-":
        month_key = utcnow().strftime("%Y-%m")

    start_dt, end_dt = _month_range_kst_naive(month_key)
    period_start_kst = start_dt.strftime("%Y-%m-%d")
    last_day = (end_dt - datetime.resolution).date()
    period_end_kst = last_day.strftime("%Y-%m-%d")

    user = User.query.get(user_pk)
    display_name = getattr(user, "nickname", None) or f"user{user_pk}"
    package_label = _safe_package_label(display_name, f"user{user_pk}")
    root_name = f"세무사전달패키지_{month_key}_{package_label}"
    download_name = f"{root_name}.zip"

    rows = (
        db.session.query(Transaction, IncomeLabel, ExpenseLabel, EvidenceItem)
        .outerjoin(IncomeLabel, and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk))
        .outerjoin(ExpenseLabel, and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk))
        .outerjoin(EvidenceItem, and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        .all()
    )

    import_job_ids = sorted({int(tx.import_job_id) for tx, _il, _el, _ev in rows if tx.import_job_id})
    import_job_map: dict[int, ImportJob] = {}
    if import_job_ids:
        for job in ImportJob.query.filter(ImportJob.id.in_(import_job_ids)).all():
            import_job_map[int(job.id)] = job

    tx_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    tx_in_count = 0
    tx_out_count = 0
    sum_in_total = 0
    sum_out_total = 0
    income_included_total = 0
    income_excluded_non_income_total = 0
    income_unknown_count = 0
    expense_business_total = 0
    expense_personal_total = 0
    expense_mixed_total = 0
    expense_unknown_total = 0
    evidence_missing_required_count = 0
    evidence_missing_required_amount = 0
    evidence_missing_maybe_count = 0
    evidence_missing_maybe_amount = 0
    evidence_attached_count = 0
    source_labels: set[str] = set()

    for tx, income_label, expense_label, evidence in rows:
        amount = _safe_int(tx.amount_krw)
        source_label, provider_label = _source_labels(tx.source)
        source_labels.add(source_label)
        import_job = import_job_map.get(int(tx.import_job_id)) if tx.import_job_id else None

        evidence_requirement = (evidence.requirement if evidence else "") or ""
        evidence_status = (evidence.status if evidence else "") or ""
        evidence_filename = (evidence.original_filename if evidence else "") or ""
        evidence_mime = (evidence.mime_type if evidence else "") or ""
        evidence_type = _evidence_type_label(evidence_mime, evidence_filename)
        evidence_zip_path = ""
        evidence_abs_path: Path | None = None
        evidence_count = 0

        if evidence and evidence.file_key and evidence.deleted_at is None:
            try:
                evidence_abs_path = resolve_file_path(evidence.file_key)
                if evidence_abs_path.exists() and evidence_abs_path.is_file():
                    safe_name = _safe_attachment_name(evidence_filename, evidence_abs_path.name)
                    evidence_zip_path = f"증빙자료/{tx.id}_{safe_name}"
                    evidence_count = 1
                    evidence_attached_count += 1
            except Exception:
                evidence_abs_path = None
                evidence_zip_path = ""
                evidence_count = 0

        tx_row = {
            "tx_id": int(tx.id),
            "occurred_at_kst": _fmt_kst(tx.occurred_at, "%Y-%m-%d %H:%M"),
            "date_kst": _fmt_kst(tx.occurred_at, "%Y-%m-%d"),
            "direction": tx.direction,
            "direction_label": "입금" if tx.direction == "in" else "출금",
            "amount_krw": amount,
            "counterparty": tx.counterparty or "",
            "memo": tx.memo or "",
            "source": tx.source or "",
            "source_label": source_label,
            "provider_label": provider_label,
            "external_hash": tx.external_hash or "",
            "import_job_id": int(tx.import_job_id) if tx.import_job_id else "",
            "import_filename": (import_job.filename if import_job else "") or "",
            "income_label_status": (income_label.status if income_label else "") or "",
            "income_label_confidence": _safe_int(income_label.confidence) if income_label else 0,
            "income_labeled_by": (income_label.labeled_by if income_label else "") or "",
            "expense_label_status": (expense_label.status if expense_label else "") or "",
            "expense_label_confidence": _safe_int(expense_label.confidence) if expense_label else 0,
            "expense_labeled_by": (expense_label.labeled_by if expense_label else "") or "",
            "evidence_id": int(evidence.id) if evidence else "",
            "evidence_requirement": evidence_requirement,
            "evidence_status": evidence_status,
            "evidence_status_label": _evidence_status_label(evidence_requirement, evidence_status),
            "evidence_note": (evidence.note if evidence else "") or "",
            "evidence_original_filename": evidence_filename,
            "evidence_mime_type": evidence_mime,
            "evidence_size_bytes": _safe_int(evidence.size_bytes) if evidence and evidence.size_bytes is not None else 0,
            "evidence_sha256": (evidence.sha256 if evidence else "") or "",
            "evidence_uploaded_at_kst": _fmt_kst(evidence.uploaded_at if evidence else None, "%Y-%m-%d %H:%M"),
            "evidence_deleted_at_kst": _fmt_kst(evidence.deleted_at if evidence else None, "%Y-%m-%d %H:%M"),
            "evidence_retention_until": evidence.retention_until.isoformat() if evidence and evidence.retention_until else "",
            "representative_evidence_type": evidence_type,
            "evidence_count": evidence_count,
            "evidence_zip_path": evidence_zip_path,
            "evidence_abs_path": evidence_abs_path,
        }

        (
            tx_row["classification_result_label"],
            tx_row["business_related_label"],
            tx_row["calculation_included_label"],
            tx_row["recheck_required"],
            tx_row["recheck_reason"],
            tx_row["trust_label"],
        ) = _classification_labels(tx_row)

        tx_row["recheck_required_label"] = "예" if tx_row["recheck_required"] else "아니오"

        if tx.direction == "in":
            tx_in_count += 1
            sum_in_total += amount
            if tx_row["income_label_status"] == "non_income":
                income_excluded_non_income_total += amount
            else:
                income_included_total += amount
                if tx_row["income_label_status"] in {"", "unknown"}:
                    income_unknown_count += 1
        else:
            tx_out_count += 1
            sum_out_total += amount
            if tx_row["expense_label_status"] == "business":
                expense_business_total += amount
            elif tx_row["expense_label_status"] == "personal":
                expense_personal_total += amount
            elif tx_row["expense_label_status"] == "mixed":
                expense_mixed_total += amount
            else:
                expense_unknown_total += amount

            if evidence_status == "missing" and evidence_requirement == "required":
                evidence_missing_required_count += 1
                evidence_missing_required_amount += amount
            elif evidence_status == "missing" and evidence_requirement == "maybe":
                evidence_missing_maybe_count += 1
                evidence_missing_maybe_amount += amount

        tx_rows.append(tx_row)

        if evidence_count == 1:
            evidence_rows.append(
                {
                    "증빙번호": tx_row["evidence_id"],
                    "연결거래번호": tx_row["tx_id"],
                    "거래일시": tx_row["occurred_at_kst"],
                    "거래처": tx_row["counterparty"],
                    "금액": tx_row["amount_krw"],
                    "증빙종류": evidence_type,
                    "파일명": evidence_filename,
                    "파일열기": ("열기", evidence_zip_path),
                    "저장위치": evidence_zip_path,
                    "업로드일시": tx_row["evidence_uploaded_at_kst"],
                    "신뢰구분": tx_row["trust_label"],
                    "계산반영여부": tx_row["calculation_included_label"],
                    "재확인필요여부": tx_row["recheck_required_label"],
                    "메모": tx_row["evidence_note"],
                    "_zip_path": evidence_zip_path,
                    "_abs_path": evidence_abs_path,
                }
            )

    review_items, evidence_missing_items, review_trade_items = _build_review_items(tx_rows)

    settings = _get_settings(user_pk)
    rate = _tax_rate(settings)
    tax_buffer_total = (
        db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
        .filter(TaxBufferLedger.user_pk == user_pk)
        .scalar()
    ) or 0
    tax_buffer_target = int(int(income_included_total) * float(rate))
    tax_buffer_shortage = max(0, int(tax_buffer_target) - int(tax_buffer_total))

    stats = PackageStats(
        month_key=month_key,
        period_start_kst=period_start_kst,
        period_end_kst=period_end_kst,
        generated_at_kst=_fmt_kst(utcnow(), "%Y-%m-%d %H:%M") or utcnow().strftime("%Y-%m-%d %H:%M"),
        tx_total=len(tx_rows),
        tx_in_count=int(tx_in_count),
        tx_out_count=int(tx_out_count),
        sum_in_total=int(sum_in_total),
        sum_out_total=int(sum_out_total),
        income_included_total=int(income_included_total),
        income_excluded_non_income_total=int(income_excluded_non_income_total),
        income_unknown_count=int(income_unknown_count),
        expense_business_total=int(expense_business_total),
        expense_personal_total=int(expense_personal_total),
        expense_mixed_total=int(expense_mixed_total),
        expense_unknown_total=int(expense_unknown_total),
        evidence_missing_required_count=int(evidence_missing_required_count),
        evidence_missing_required_amount=int(evidence_missing_required_amount),
        evidence_missing_maybe_count=int(evidence_missing_maybe_count),
        evidence_missing_maybe_amount=int(evidence_missing_maybe_amount),
        evidence_attached_count=int(evidence_attached_count),
        review_needed_count=len(review_items),
        tax_rate=float(rate),
        tax_buffer_total=int(tax_buffer_total),
        tax_buffer_target=int(tax_buffer_target),
        tax_buffer_shortage=int(tax_buffer_shortage),
    )

    return PackageSnapshot(
        root_name=root_name,
        download_name=download_name,
        display_name=display_name,
        stats=stats,
        transactions=tx_rows,
        evidences=evidence_rows,
        review_items=review_items,
        evidence_missing_items=evidence_missing_items,
        review_trade_items=review_trade_items,
        included_source_labels=sorted(source_labels),
    )


def _write_table_sheet(ws, headers: list[str], rows: list[dict[str, Any]], freeze: str = "A2") -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = TOP_ALIGN

    for row in rows:
        values = []
        links: list[tuple[int, str]] = []
        for idx, header in enumerate(headers, start=1):
            value = row.get(header, "")
            if isinstance(value, tuple) and len(value) == 2:
                display, target = value
                values.append(display)
                if target:
                    links.append((idx, target))
            else:
                values.append(value)
        ws.append(values)
        current_row = ws.max_row
        for col_idx, target in links:
            cell = ws.cell(current_row, col_idx)
            cell.hyperlink = target
            cell.style = "Hyperlink"
        for cell in ws[current_row]:
            cell.alignment = TOP_ALIGN

    ws.freeze_panes = freeze
    if rows:
        ws.auto_filter.ref = ws.dimensions
    _autosize(ws)


def _autosize(ws) -> None:
    for column_cells in ws.columns:
        values = ["" if c.value is None else str(c.value) for c in column_cells]
        width = min(max((len(v) for v in values), default=10) + 2, 40)
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = max(10, width)


def _workbook_bytes(builder) -> bytes:
    wb = builder()
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _build_summary_workbook(snapshot: PackageSnapshot) -> bytes:
    stats = snapshot.stats

    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "전체요약"
        summary_rows = [
            {"항목명": "사용자명", "값": snapshot.display_name},
            {"항목명": "대상 기간", "값": f"{stats.period_start_kst} ~ {stats.period_end_kst}"},
            {"항목명": "생성일시", "값": stats.generated_at_kst},
            {"항목명": "총 거래 수", "값": stats.tx_total},
            {"항목명": "총 수입", "값": stats.sum_in_total},
            {"항목명": "총 지출", "값": stats.sum_out_total},
            {"항목명": "업무 관련 지출 합계", "값": stats.expense_business_total},
            {"항목명": "증빙 첨부 수", "값": stats.evidence_attached_count},
            {"항목명": "확인 필요 항목 수", "값": stats.review_needed_count},
            {"항목명": "참고", "값": "공식자료는 현재 패키지 범위에서 제외됩니다."},
        ]
        _write_table_sheet(ws, ["항목명", "값"], summary_rows)

        ws2 = wb.create_sheet("신뢰구분안내")
        _write_table_sheet(
            ws2,
            ["구분", "의미", "계산 반영 여부", "세무사 확인 필요 여부", "예시"],
            [
                {
                    "구분": "반영됨",
                    "의미": "구조화된 거래가 분류 완료되고 필요한 증빙 상태가 정리된 항목",
                    "계산 반영 여부": "예 또는 아니오",
                    "세무사 확인 필요 여부": "낮음",
                    "예시": "자동연동/수동업로드 거래 + 분류 완료 + 증빙 첨부 또는 불필요",
                },
                {
                    "구분": "참고용",
                    "의미": "현재 자료는 있으나 구조화 근거가 상대적으로 약한 항목",
                    "계산 반영 여부": "예/아니오를 함께 표기",
                    "세무사 확인 필요 여부": "보통",
                    "예시": "수동입력 거래처럼 사용자가 직접 입력한 거래",
                },
                {
                    "구분": "재확인필요",
                    "의미": "분류 미확정, 필수 증빙 누락, 혼합 판단 등으로 추가 확인이 필요한 항목",
                    "계산 반영 여부": "보류 중심",
                    "세무사 확인 필요 여부": "높음",
                    "예시": "지출 분류 미확정 거래, 필수 증빙 미첨부 거래",
                },
            ],
        )

        tx_status = _count_by_trust(snapshot.transactions)
        ev_status = _count_by_trust(snapshot.evidences)
        review_status = _count_by_trust(snapshot.review_items)
        ws3 = wb.create_sheet("반영현황")
        _write_table_sheet(
            ws3,
            ["자료 구분", "개수", "반영 건수", "참고용 건수", "재확인 건수"],
            [
                {"자료 구분": "거래내역", "개수": len(snapshot.transactions), "반영 건수": tx_status["반영됨"], "참고용 건수": tx_status["참고용"], "재확인 건수": tx_status["재확인필요"]},
                {"자료 구분": "증빙자료", "개수": len(snapshot.evidences), "반영 건수": ev_status["반영됨"], "참고용 건수": ev_status["참고용"], "재확인 건수": ev_status["재확인필요"]},
                {"자료 구분": "확인필요항목", "개수": len(snapshot.review_items), "반영 건수": review_status["반영됨"], "참고용 건수": review_status["참고용"], "재확인 건수": review_status["재확인필요"]},
            ],
        )

        ws4 = wb.create_sheet("기본정보")
        _write_table_sheet(
            ws4,
            ["항목명", "값"],
            [
                {"항목명": "패키지 버전", "값": PACKAGE_VERSION},
                {"항목명": "생성 기준 월", "값": stats.month_key},
                {"항목명": "포함 파일 수", "값": 6},
                {"항목명": "포함 증빙 수", "값": len(snapshot.evidences)},
                {"항목명": "연동 포함 여부", "값": ", ".join(snapshot.included_source_labels) if snapshot.included_source_labels else "없음"},
            ],
        )
        return wb

    return _workbook_bytes(build)


def _count_by_trust(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"반영됨": 0, "참고용": 0, "재확인필요": 0}
    for row in rows:
        label = row.get("trust_label") or row.get("신뢰구분") or "재확인필요"
        if label not in counts:
            label = "재확인필요"
        counts[label] += 1
    return counts


def _build_transactions_workbook(snapshot: PackageSnapshot) -> bytes:
    stats = snapshot.stats

    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "거래정리"
        rows = []
        for tx in snapshot.transactions:
            rows.append(
                {
                    "거래번호": tx.get("tx_id", ""),
                    "거래일시": tx.get("occurred_at_kst", ""),
                    "입출금구분": tx.get("direction_label", ""),
                    "금액": tx.get("amount_krw", 0),
                    "거래처": tx.get("counterparty", ""),
                    "적요": tx.get("memo", ""),
                    "자료출처": tx.get("source_label", ""),
                    "연동공급자": tx.get("provider_label", ""),
                    "분류결과": tx.get("classification_result_label", ""),
                    "업무관련여부": tx.get("business_related_label", ""),
                    "증빙상태": tx.get("evidence_status_label", ""),
                    "대표증빙종류": tx.get("representative_evidence_type", ""),
                    "증빙개수": tx.get("evidence_count", 0),
                    "증빙바로열기": ("열기", tx.get("evidence_zip_path")) if tx.get("evidence_zip_path") else "",
                    "신뢰구분": tx.get("trust_label", "재확인필요"),
                    "계산반영여부": tx.get("calculation_included_label", "보류"),
                    "재확인필요여부": tx.get("recheck_required_label", "아니오"),
                    "재확인사유": tx.get("recheck_reason", ""),
                    "메모": tx.get("evidence_note", ""),
                }
            )
        _write_table_sheet(
            ws,
            [
                "거래번호",
                "거래일시",
                "입출금구분",
                "금액",
                "거래처",
                "적요",
                "자료출처",
                "연동공급자",
                "분류결과",
                "업무관련여부",
                "증빙상태",
                "대표증빙종류",
                "증빙개수",
                "증빙바로열기",
                "신뢰구분",
                "계산반영여부",
                "재확인필요여부",
                "재확인사유",
                "메모",
            ],
            rows,
        )

        ws2 = wb.create_sheet("거래원본")
        raw_rows = []
        for tx in snapshot.transactions:
            raw_rows.append(
                {
                    "거래번호": tx.get("tx_id", ""),
                    "원본자료유형": tx.get("source_label", ""),
                    "원본파일명(있으면)": tx.get("import_filename", ""),
                    "원본행번호(있으면)": "",
                    "원본거래일시": tx.get("occurred_at_kst", ""),
                    "원본금액": tx.get("amount_krw", 0),
                    "원본거래처": tx.get("counterparty", ""),
                    "정규화메모": tx.get("memo", ""),
                }
            )
        _write_table_sheet(
            ws2,
            ["거래번호", "원본자료유형", "원본파일명(있으면)", "원본행번호(있으면)", "원본거래일시", "원본금액", "원본거래처", "정규화메모"],
            raw_rows,
        )

        ws3 = wb.create_sheet("월별요약")
        _write_table_sheet(
            ws3,
            ["항목", "값"],
            [
                {"항목": "대상 월", "값": stats.month_key},
                {"항목": "총 거래 수", "값": stats.tx_total},
                {"항목": "총 수입", "값": stats.sum_in_total},
                {"항목": "총 지출", "값": stats.sum_out_total},
                {"항목": "업무 관련 지출", "값": stats.expense_business_total},
                {"항목": "첨부된 증빙 수", "값": stats.evidence_attached_count},
                {"항목": "확인 필요 항목 수", "값": stats.review_needed_count},
            ],
        )

        ws4 = wb.create_sheet("분류요약")
        summary_map: dict[tuple[str, str], dict[str, int]] = {}
        for tx in snapshot.transactions:
            key = (tx.get("classification_result_label", ""), tx.get("trust_label", "재확인필요"))
            bucket = summary_map.setdefault(key, {"count": 0, "amount": 0})
            bucket["count"] += 1
            bucket["amount"] += int(tx.get("amount_krw", 0))
        rows4 = []
        for (classification, trust), bucket in sorted(summary_map.items()):
            rows4.append(
                {
                    "분류결과": classification,
                    "신뢰구분": trust,
                    "거래 수": bucket["count"],
                    "금액 합계": bucket["amount"],
                }
            )
        _write_table_sheet(ws4, ["분류결과", "신뢰구분", "거래 수", "금액 합계"], rows4)
        return wb

    return _workbook_bytes(build)


def _build_evidence_workbook(snapshot: PackageSnapshot) -> bytes:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "증빙목록"
        evidence_rows = []
        for evidence in snapshot.evidences:
            evidence_rows.append(
                {
                    "증빙번호": evidence.get("증빙번호", ""),
                    "연결거래번호": evidence.get("연결거래번호", ""),
                    "증빙종류": evidence.get("증빙종류", "증빙파일"),
                    "파일명": evidence.get("파일명", ""),
                    "파일열기": evidence.get("파일열기", ""),
                    "저장위치": evidence.get("저장위치", ""),
                    "업로드일시": evidence.get("업로드일시", ""),
                    "신뢰구분": evidence.get("신뢰구분", "재확인필요"),
                    "계산반영여부": evidence.get("계산반영여부", "보류"),
                    "재확인필요여부": evidence.get("재확인필요여부", "아니오"),
                    "메모": evidence.get("메모", ""),
                }
            )
        _write_table_sheet(
            ws,
            ["증빙번호", "연결거래번호", "증빙종류", "파일명", "파일열기", "저장위치", "업로드일시", "신뢰구분", "계산반영여부", "재확인필요여부", "메모"],
            evidence_rows,
        )

        ws2 = wb.create_sheet("거래별증빙연결")
        linked_rows = []
        for tx in snapshot.transactions:
            linked_rows.append(
                {
                    "거래번호": tx.get("tx_id", ""),
                    "거래일시": tx.get("occurred_at_kst", ""),
                    "거래처": tx.get("counterparty", ""),
                    "금액": tx.get("amount_krw", 0),
                    "증빙상태": tx.get("evidence_status_label", ""),
                    "대표증빙종류": tx.get("representative_evidence_type", "") if tx.get("evidence_count") else "",
                    "증빙개수": tx.get("evidence_count", 0),
                    "대표증빙열기": ("열기", tx.get("evidence_zip_path")) if tx.get("evidence_zip_path") else "",
                }
            )
        _write_table_sheet(
            ws2,
            ["거래번호", "거래일시", "거래처", "금액", "증빙상태", "대표증빙종류", "증빙개수", "대표증빙열기"],
            linked_rows,
        )

        ws3 = wb.create_sheet("증빙요약")
        summary = {}
        for tx in snapshot.transactions:
            key = (
                tx.get("evidence_status_label", ""),
                tx.get("representative_evidence_type", "") if tx.get("evidence_count") else "미첨부",
            )
            summary[key] = summary.get(key, 0) + 1
        rows3 = []
        for (status, ev_type), count in sorted(summary.items()):
            rows3.append({"증빙상태": status, "증빙종류": ev_type, "개수": count})
        _write_table_sheet(ws3, ["증빙상태", "증빙종류", "개수"], rows3)
        return wb

    return _workbook_bytes(build)


def _build_review_workbook(snapshot: PackageSnapshot) -> bytes:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "확인필요항목"
        _write_table_sheet(
            ws,
            ["항목번호", "항목유형", "관련자료구분", "관련번호", "요약설명", "현재상태", "필요한확인내용", "우선순위", "메모"],
            snapshot.review_items,
        )

        ws2 = wb.create_sheet("증빙누락")
        _write_table_sheet(
            ws2,
            ["거래번호", "거래일시", "거래처", "금액", "증빙상태", "필요한확인내용", "우선순위"],
            snapshot.evidence_missing_items,
        )

        ws3 = wb.create_sheet("검토필요거래")
        _write_table_sheet(
            ws3,
            ["거래번호", "거래일시", "자료출처", "거래처", "금액", "현재상태", "재확인사유", "필요한확인내용"],
            snapshot.review_trade_items,
        )
        return wb

    return _workbook_bytes(build)


def _render_package_guide(snapshot: PackageSnapshot) -> str:
    stats = snapshot.stats
    lines = [
        "[쓸수있어(SafeToSpend) 거래+증빙 중심 세무사 전달 패키지]",
        f"- 패키지 버전: {PACKAGE_VERSION}",
        f"- 대상 기간: {stats.period_start_kst} ~ {stats.period_end_kst}",
        f"- 생성 시각(KST): {stats.generated_at_kst}",
        f"- 사용자명: {snapshot.display_name}",
        "",
        "[포함 파일]",
        "- 00_패키지안내.txt : 현재 패키지 범위와 한계, 신뢰 구분 안내",
        "- 01_패키지요약.xlsx : 전체 요약, 신뢰 구분 안내, 반영 현황",
        "- 02_거래정리.xlsx : 거래 목록, 원본 정보, 분류/증빙 연결",
        "- 03_증빙목록.xlsx : 첨부된 증빙 목록과 거래별 연결",
        "- 05_확인필요항목.xlsx : 필수 누락/분류 미확정 등 재확인 목록",
        "- 증빙자료/ : 현재 연결된 대표 증빙 파일",
        "",
        "[현재 포함되는 자료 범위]",
        "- 수동입력 거래",
        "- 수동업로드(CSV) 거래",
        "- 자동연동 거래",
        "- 거래에 연결된 대표 증빙 1개",
        "- 누락/검토 필요 상태",
        "",
        "[현재 포함되지 않는 자료 범위]",
        "- 공식자료 목록/공식자료 폴더",
        "- 참고자료 폴더",
        "- 추가설명 폴더",
        "- 거래당 다중 증빙 구조",
        "",
        "[신뢰 구분 기준]",
        "- 반영됨: 구조화된 거래가 분류 완료되고 필요한 증빙 상태가 정리된 항목",
        "- 참고용: 수동입력처럼 구조화 근거가 상대적으로 약한 항목",
        "- 재확인필요: 분류 미확정, 필수 증빙 누락, 혼합 판단 등 추가 확인이 필요한 항목",
        "",
        "[현재 한계]",
        "- 거래당 대표 증빙 1개 기준으로 정리됩니다.",
        "- 공식자료 교차검증은 현재 패키지 범위에 포함되지 않습니다.",
        "- ZIP 내부 링크는 압축을 푼 뒤 여는 방식이 가장 안정적입니다.",
    ]
    return "\n".join(lines) + "\n"


def build_tax_package_zip_from_snapshot(snapshot: PackageSnapshot) -> tuple[io.BytesIO, str]:
    out = io.BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        root = snapshot.root_name
        zf.writestr(f"{root}/00_패키지안내.txt", _render_package_guide(snapshot))
        zf.writestr(f"{root}/01_패키지요약.xlsx", _build_summary_workbook(snapshot))
        zf.writestr(f"{root}/02_거래정리.xlsx", _build_transactions_workbook(snapshot))
        zf.writestr(f"{root}/03_증빙목록.xlsx", _build_evidence_workbook(snapshot))
        zf.writestr(f"{root}/05_확인필요항목.xlsx", _build_review_workbook(snapshot))
        zf.writestr(f"{root}/증빙자료/", b"")

        for evidence in snapshot.evidences:
            zip_path = evidence.get("_zip_path") or ""
            abs_path = evidence.get("_abs_path")
            if not zip_path or not abs_path:
                continue
            try:
                with Path(abs_path).open("rb") as f:
                    zf.writestr(f"{root}/{zip_path}", f.read())
            except Exception:
                continue

    out.seek(0)
    return out, snapshot.download_name


def build_tax_package_zip(user_pk: int, month_key: str) -> tuple[io.BytesIO, str]:
    snapshot = _collect_package_snapshot(user_pk=user_pk, month_key=month_key)
    return build_tax_package_zip_from_snapshot(snapshot)
