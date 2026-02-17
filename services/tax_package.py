# services/tax_package.py
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func
from werkzeug.utils import secure_filename

from core.extensions import db
from core.time import utcnow
from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    SafeToSpendSettings,
    TaxBufferLedger,
    Transaction,
)
from services.evidence_vault import resolve_file_path


KST = ZoneInfo("Asia/Seoul")


# -----------------------------
# Month utils
# -----------------------------
def _month_range_kst_naive(month_key: str) -> tuple[datetime, datetime]:
    """month_key(YYYY-MM)의 '한국시간 월 경계'를 **naive datetime** 범위로 반환.

    프로젝트 전반(캘린더/보관함/리포트)에서 occurred_at을 timezone 없는 datetime으로 쓰는 전제가 많아서,
    여기서는 KST 기준 월 경계를 naive로 맞춘다.
    """

    y, m = month_key.split("-")
    y = int(y)
    m = int(m)

    start = datetime(y, m, 1, 0, 0, 0)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0)
    return start, end


def _ensure_settings(user_pk: int) -> SafeToSpendSettings:
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()
    return s


def _tax_rate(s: SafeToSpendSettings) -> float:
    r = float(getattr(s, "default_tax_rate", 0.15) or 0.15)
    # 15(%) 형태도 들어올 수 있으니 보정
    if r > 1:
        r = r / 100.0
    return max(0.0, min(r, 0.95))


def _krw(n: int) -> str:
    return f"{int(n or 0):,}원"


def _to_kst(dt: datetime | None) -> datetime | None:
    if not dt:
        return None

    # 프로젝트의 DateTime 컬럼들이 tz 없는 값이 많은 편
    # → tz 없는 값은 KST로 간주(화면/월경계/사용자 체감과 일치)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _fmt_kst(dt: datetime | None, fmt: str) -> str:
    dtk = _to_kst(dt)
    return dtk.strftime(fmt) if dtk else ""


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


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

    tax_rate: float
    tax_buffer_total: int
    tax_buffer_target: int
    tax_buffer_shortage: int


# -----------------------------
# Public API
# -----------------------------
def build_tax_package_zip(user_pk: int, month_key: str) -> tuple[io.BytesIO, str]:
    """월별 세무사 전달 패키지(zip)를 메모리에서 생성해 반환.

    ZIP 구조(루트 폴더 포함):
    - SafeToSpend_TaxPackage_<YYYY-MM>/
      - README_세무사용.txt
      - manifest.json
      - transactions.csv
      - evidence_index.csv
      - missing_evidence.csv
      - attachments/ ... (가능한 경우 실제 파일 포함)

    ✅ reportlab 같은 외부 PDF 의존성 없이 동작하도록 설계(로컬 환경에서 바로 실행 가능).
    """

    month_key = (month_key or "").strip()
    if not month_key or len(month_key) != 7 or month_key[4] != "-":
        month_key = utcnow().strftime("%Y-%m")

    start_dt, end_dt = _month_range_kst_naive(month_key)

    # 안내문/manifest는 KST 표기
    period_start_kst = start_dt.strftime("%Y-%m-%d")
    try:
        last_day = (end_dt - datetime.resolution).date()
        period_end_kst = last_day.strftime("%Y-%m-%d")
    except Exception:
        period_end_kst = ""

    # ---- Load transactions (month scoped) + labels/evidence
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

    # ---- Totals / Records
    tx_records: list[dict[str, Any]] = []
    evidence_index_records: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []  # {zip_path, abs_path}

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

    ev_req_cnt = 0
    ev_req_amt = 0
    ev_maybe_cnt = 0
    ev_maybe_amt = 0

    for tx, il, el, ev in rows:
        amt = _safe_int(tx.amount_krw)
        occurred_kst_str = _fmt_kst(tx.occurred_at, "%Y-%m-%d %H:%M")
        date_kst_str = _fmt_kst(tx.occurred_at, "%Y-%m-%d")

        if tx.direction == "in":
            tx_in_count += 1
            sum_in_total += amt
        else:
            tx_out_count += 1
            sum_out_total += amt

        income_status = (il.status if il else "") if tx.direction == "in" else ""
        income_conf = _safe_int(il.confidence) if il and tx.direction == "in" else ""
        income_by = (il.labeled_by if il else "") if tx.direction == "in" else ""

        expense_status = (el.status if el else "") if tx.direction == "out" else ""
        expense_conf = _safe_int(el.confidence) if el and tx.direction == "out" else ""
        expense_by = (el.labeled_by if el else "") if tx.direction == "out" else ""

        ev_req = (ev.requirement if ev else "") if tx.direction == "out" else ""
        ev_status = (ev.status if ev else "") if tx.direction == "out" else ""
        ev_note = (ev.note if ev else "") if tx.direction == "out" else ""

        # ---- income totals
        if tx.direction == "in":
            if income_status == "non_income":
                income_excluded_non_income_total += amt
            else:
                income_included_total += amt
                if not income_status or income_status == "unknown":
                    income_unknown_count += 1

        # ---- expense totals
        if tx.direction == "out":
            if expense_status == "business":
                expense_business_total += amt
            elif expense_status == "personal":
                expense_personal_total += amt
            elif expense_status == "mixed":
                expense_mixed_total += amt
            else:
                expense_unknown_total += amt

            # evidence missing (required/maybe)
            if ev_status == "missing" and ev_req in ("required", "maybe"):
                if ev_req == "required":
                    ev_req_cnt += 1
                    ev_req_amt += amt
                else:
                    ev_maybe_cnt += 1
                    ev_maybe_amt += amt

        # ---- attachment path inside zip (if exists)
        attachment_zip_path = ""
        if tx.direction == "out" and ev and ev.file_key and (ev.deleted_at is None):
            try:
                abs_path = resolve_file_path(ev.file_key)
                if abs_path.exists() and abs_path.is_file():
                    base = secure_filename(ev.original_filename or "evidence")
                    if not base:
                        base = "evidence"
                    # tx_id prefix로 중복/충돌 방지
                    base = f"{tx.id}_{base}"
                    attachment_zip_path = f"attachments/{base}"
                    attachments.append({"zip_path": attachment_zip_path, "abs_path": abs_path})
            except Exception:
                attachment_zip_path = ""

        # ---- transactions.csv row
        tx_records.append(
            {
                "tx_id": tx.id,
                "occurred_at_kst": occurred_kst_str,
                "date_kst": date_kst_str,
                "direction": tx.direction,
                "amount_krw": amt,
                "counterparty": tx.counterparty or "",
                "memo": tx.memo or "",
                "source": tx.source or "",
                "external_hash": tx.external_hash or "",
                "income_label_status": income_status or "",
                "income_label_confidence": income_conf,
                "income_labeled_by": income_by or "",
                "expense_label_status": expense_status or "",
                "expense_label_confidence": expense_conf,
                "expense_labeled_by": expense_by or "",
                "evidence_requirement": ev_req or "",
                "evidence_status": ev_status or "",
                "evidence_note": ev_note or "",
                "evidence_original_filename": (ev.original_filename if ev else "") or "",
                "evidence_sha256": (ev.sha256 if ev else "") or "",
                "evidence_uploaded_at_kst": _fmt_kst(ev.uploaded_at if ev else None, "%Y-%m-%d %H:%M"),
                "attachment_zip_path": attachment_zip_path,
            }
        )

        # ---- evidence_index.csv row (out only)
        if tx.direction == "out" and ev:
            evidence_index_records.append(
                {
                    "tx_id": tx.id,
                    "requirement": ev.requirement or "",
                    "status": ev.status or "",
                    "note": ev.note or "",
                    "file_key": ev.file_key or "",
                    "original_filename": ev.original_filename or "",
                    "mime_type": ev.mime_type or "",
                    "size_bytes": _safe_int(ev.size_bytes, 0) if ev.size_bytes is not None else "",
                    "sha256": ev.sha256 or "",
                    "uploaded_at_kst": _fmt_kst(ev.uploaded_at, "%Y-%m-%d %H:%M"),
                    "retention_until": (ev.retention_until.isoformat() if isinstance(ev.retention_until, date) else ""),
                    "deleted_at_kst": _fmt_kst(ev.deleted_at, "%Y-%m-%d %H:%M"),
                    "attachment_zip_path": attachment_zip_path,
                }
            )

    # ---- settings/tax buffer
    s = _ensure_settings(user_pk)
    rate = _tax_rate(s)

    tax_buffer_total = (
        db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
        .filter(TaxBufferLedger.user_pk == user_pk)
        .scalar()
    ) or 0

    # 권장액(단순): 포함 수입(included) * 세율
    tax_buffer_target = int(int(income_included_total) * float(rate))
    tax_buffer_shortage = max(0, int(tax_buffer_target) - int(tax_buffer_total))

    generated_at_kst = _fmt_kst(utcnow(), "%Y-%m-%d %H:%M") or utcnow().strftime("%Y-%m-%d %H:%M")

    stats = PackageStats(
        month_key=month_key,
        period_start_kst=period_start_kst,
        period_end_kst=period_end_kst,
        generated_at_kst=generated_at_kst,
        tx_total=len(tx_records),
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
        evidence_missing_required_count=int(ev_req_cnt),
        evidence_missing_required_amount=int(ev_req_amt),
        evidence_missing_maybe_count=int(ev_maybe_cnt),
        evidence_missing_maybe_amount=int(ev_maybe_amt),
        tax_rate=float(rate),
        tax_buffer_total=int(tax_buffer_total),
        tax_buffer_target=int(tax_buffer_target),
        tax_buffer_shortage=int(tax_buffer_shortage),
    )

    missing_list = _build_missing_list(tx_records)

    # ---- ZIP build
    root = f"SafeToSpend_TaxPackage_{month_key}"
    out = io.BytesIO()
    z = zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED)

    def _wtext(rel_path: str, text: str) -> None:
        z.writestr(f"{root}/{rel_path}", text)

    def _wjson(rel_path: str, payload: dict[str, Any]) -> None:
        _wtext(rel_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _wcsv(rel_path: str, header: list[str], records: list[dict[str, Any]]) -> None:
        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(header)
        for r in records:
            writer.writerow([r.get(h, "") for h in header])
        # Excel-friendly UTF-8 with BOM
        z.writestr(f"{root}/{rel_path}", buf.getvalue().encode("utf-8-sig"))

    # 1) README (세무사용 안내문)
    _wtext("README_세무사용.txt", _render_accountant_readme(stats))

    # 2) manifest
    manifest = {
        "schema": "SafeToSpend_TaxPackage",
        "schema_version": 1,
        "month_key": stats.month_key,
        "generated_at_kst": stats.generated_at_kst,
        "period_kst": {"start": stats.period_start_kst, "end": stats.period_end_kst},
        "counts": {
            "transactions_total": stats.tx_total,
            "transactions_in": stats.tx_in_count,
            "transactions_out": stats.tx_out_count,
            "income_unknown_count": stats.income_unknown_count,
            "evidence_missing_required_count": stats.evidence_missing_required_count,
            "evidence_missing_maybe_count": stats.evidence_missing_maybe_count,
        },
        "sums_krw": {
            "sum_in_total": stats.sum_in_total,
            "sum_out_total": stats.sum_out_total,
            "income_included_total": stats.income_included_total,
            "income_excluded_non_income_total": stats.income_excluded_non_income_total,
            "expense_business_total": stats.expense_business_total,
            "expense_personal_total": stats.expense_personal_total,
            "expense_mixed_total": stats.expense_mixed_total,
            "expense_unknown_total": stats.expense_unknown_total,
            "evidence_missing_required_amount": stats.evidence_missing_required_amount,
            "evidence_missing_maybe_amount": stats.evidence_missing_maybe_amount,
        },
        "tax_buffer": {
            "default_tax_rate": stats.tax_rate,
            "tax_buffer_total": stats.tax_buffer_total,
            "tax_buffer_target": stats.tax_buffer_target,
            "tax_buffer_shortage": stats.tax_buffer_shortage,
        },
        "files": {
            "transactions_csv": "transactions.csv",
            "evidence_index_csv": "evidence_index.csv",
            "missing_evidence_csv": "missing_evidence.csv",
            "attachments_dir": "attachments/",
        },
        "notes": {
            "generated_by": "SafeToSpend(쓸수있어)",
            "disclaimer": "본 자료는 사용자가 제공/연동한 거래 및 업로드된 증빙을 정리한 참고 자료이며, 최종 신고 판단은 세무사/국세청 기준에 따릅니다.",
        },
    }
    _wjson("manifest.json", manifest)

    # 3) CSVs
    tx_header = [
        "tx_id",
        "occurred_at_kst",
        "date_kst",
        "direction",
        "amount_krw",
        "counterparty",
        "memo",
        "source",
        "external_hash",
        "income_label_status",
        "income_label_confidence",
        "income_labeled_by",
        "expense_label_status",
        "expense_label_confidence",
        "expense_labeled_by",
        "evidence_requirement",
        "evidence_status",
        "evidence_note",
        "evidence_original_filename",
        "evidence_sha256",
        "evidence_uploaded_at_kst",
        "attachment_zip_path",
    ]
    _wcsv("transactions.csv", tx_header, tx_records)

    ev_header = [
        "tx_id",
        "requirement",
        "status",
        "note",
        "file_key",
        "original_filename",
        "mime_type",
        "size_bytes",
        "sha256",
        "uploaded_at_kst",
        "retention_until",
        "deleted_at_kst",
        "attachment_zip_path",
    ]
    _wcsv("evidence_index.csv", ev_header, evidence_index_records)

    miss_header = [
        "priority",
        "tx_id",
        "date_kst",
        "amount_krw",
        "counterparty",
        "memo",
        "requirement",
        "why",
        "next_action",
    ]
    _wcsv("missing_evidence.csv", miss_header, missing_list[:200])

    # 4) attachments
    _wtext("attachments/README.txt", "증빙 파일(첨부된 경우)이 이 폴더에 포함됩니다.\n")
    for a in attachments:
        try:
            zip_path = a["zip_path"]
            abs_path = a["abs_path"]
            with abs_path.open("rb") as f:
                z.writestr(f"{root}/{zip_path}", f.read())
        except Exception:
            continue

    z.close()
    out.seek(0)

    filename = f"SafeToSpend_TaxPackage_{month_key}.zip"
    return out, filename


# -----------------------------
# Helpers
# -----------------------------
def _build_missing_list(tx_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """월 거래 중 '사용자/세무사 액션이 필요한 것'만 뽑아 우선순위 리스트로."""

    candidates: list[dict[str, Any]] = []

    for r in tx_records:
        direction = r.get("direction")
        amt = _safe_int(r.get("amount_krw"))
        cp = r.get("counterparty") or ""
        memo = r.get("memo") or ""
        date_kst = r.get("date_kst") or ""
        tx_id = r.get("tx_id")

        # P0: 필수 증빙 누락
        if (
            direction == "out"
            and r.get("evidence_status") == "missing"
            and r.get("evidence_requirement") == "required"
        ):
            candidates.append(
                {
                    "priority": "P0",
                    "tx_id": tx_id,
                    "date_kst": date_kst,
                    "amount_krw": amt,
                    "counterparty": cp,
                    "memo": memo,
                    "requirement": "required",
                    "why": "필수 증빙 누락",
                    "next_action": "카드전표/현금영수증/세금계산서 등 첨부 또는 '불필요'로 처리",
                    "_score": 100_000_000 + amt,
                }
            )
            continue

        # P1: 수입 분류 미확정
        if direction == "in" and (not r.get("income_label_status") or r.get("income_label_status") == "unknown"):
            candidates.append(
                {
                    "priority": "P1",
                    "tx_id": tx_id,
                    "date_kst": date_kst,
                    "amount_krw": amt,
                    "counterparty": cp,
                    "memo": memo,
                    "requirement": "",
                    "why": "수입/비수입 미확정",
                    "next_action": "수입이면 '수입' 확정, 아니면 '비수입'으로 표시",
                    "_score": 80_000_000 + amt,
                }
            )
            continue

        # P1: 지출 분류 미확정/혼재
        if direction == "out" and (not r.get("expense_label_status") or r.get("expense_label_status") in ("unknown", "mixed")):
            candidates.append(
                {
                    "priority": "P1",
                    "tx_id": tx_id,
                    "date_kst": date_kst,
                    "amount_krw": amt,
                    "counterparty": cp,
                    "memo": memo,
                    "requirement": "",
                    "why": "지출(업무/개인) 미확정",
                    "next_action": "업무/개인/혼합 중 하나로 확정",
                    "_score": 70_000_000 + amt,
                }
            )
            continue

        # P2: 검토 증빙 누락
        if (
            direction == "out"
            and r.get("evidence_status") == "missing"
            and r.get("evidence_requirement") == "maybe"
        ):
            candidates.append(
                {
                    "priority": "P2",
                    "tx_id": tx_id,
                    "date_kst": date_kst,
                    "amount_krw": amt,
                    "counterparty": cp,
                    "memo": memo,
                    "requirement": "maybe",
                    "why": "증빙 확인 필요(미첨부)",
                    "next_action": "업무비면 증빙 첨부, 개인/불필요면 표시",
                    "_score": 60_000_000 + amt,
                }
            )

    candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)

    out: list[dict[str, Any]] = []
    for c in candidates:
        c.pop("_score", None)
        out.append(c)
    return out


def _render_accountant_readme(stats: PackageStats) -> str:
    """세무사용 안내문(텍스트 1장 분량). 과장 없이, 실무 흐름에 맞춰 작성."""

    est_profit = stats.income_included_total - stats.expense_business_total
    has_uncertain = (
        stats.income_unknown_count
        + (1 if stats.expense_mixed_total > 0 else 0)
        + (1 if stats.expense_unknown_total > 0 else 0)
        + stats.evidence_missing_required_count
        + stats.evidence_missing_maybe_count
    ) > 0

    lines: list[str] = []
    lines.append("[쓸수있어(SafeToSpend) 세무사 전달 패키지]")
    lines.append(f"- 대상 월: {stats.month_key}")
    lines.append(f"- 기간(KST): {stats.period_start_kst} ~ {stats.period_end_kst}")
    lines.append(f"- 생성일(KST): {stats.generated_at_kst}")
    lines.append("")

    lines.append("1) 포함 파일")
    lines.append("- transactions.csv : 거래 원장(입/출금) + 분류(수입/지출) + 증빙 상태")
    lines.append("- evidence_index.csv : 증빙(첨부/누락/보관) 인덱스")
    lines.append("- missing_evidence.csv : 우선 처리(필수 누락/미확정) 리스트")
    lines.append("- attachments/ : 사용자가 실제 업로드한 증빙 파일(있는 경우)")
    lines.append("- manifest.json : 패키지 메타/집계(검증용)")
    lines.append("")

    lines.append("2) 핵심 요약(참고용)")
    lines.append(f"- 총 입금 합계: {_krw(stats.sum_in_total)}")
    lines.append(f"- 총 출금 합계: {_krw(stats.sum_out_total)}")
    lines.append(f"- 포함 수입(비수입 제외): {_krw(stats.income_included_total)}")
    lines.append(f"- 비수입(제외): {_krw(stats.income_excluded_non_income_total)}")
    lines.append(f"- 사업 경비(업무 확정): {_krw(stats.expense_business_total)}")
    lines.append(f"- 순이익(단순 추정): {_krw(est_profit)}")
    lines.append("  * 위 추정치는 분류/증빙 상태에 따라 달라질 수 있습니다.")
    lines.append("")

    lines.append("3) 확인이 필요한 항목")
    lines.append(f"- 증빙 누락(필수): {stats.evidence_missing_required_count}건 / {_krw(stats.evidence_missing_required_amount)}")
    lines.append(f"- 증빙 확인(검토): {stats.evidence_missing_maybe_count}건 / {_krw(stats.evidence_missing_maybe_amount)}")
    lines.append(f"- 수입 미확정: {stats.income_unknown_count}건")
    lines.append(f"- 경비 혼재/미확정: {_krw(stats.expense_mixed_total + stats.expense_unknown_total)}")
    lines.append(f"- 상태: {'추정(검토 필요 포함)' if has_uncertain else '확정(검토 필요 0)'}")
    lines.append("")

    lines.append("4) 사용 방법(권장)")
    lines.append("- (A) missing_evidence.csv의 P0(필수 누락)부터 우선 확인")
    lines.append("- (B) transactions.csv에서 업무경비(business) 기준으로 필요 경비 취합")
    lines.append("- (C) attachments/에 있는 파일은 tx_id로 거래와 매칭 가능")
    lines.append("- (D) 분류/증빙은 사용자 입력 기반이므로, 최종 확정은 세무사 판단")
    lines.append("")

    lines.append("[주의]")
    lines.append("- 본 자료는 사용자가 제공/연동한 거래 및 업로드된 증빙을 정리한 참고 자료입니다.")
    lines.append("- 누락/미확정 항목이 있으면 추정치가 포함될 수 있습니다.")
    lines.append("- 최종 신고 판단 및 세법 적용은 세무사/국세청 기준에 따릅니다.")

    return "\n".join(lines) + "\n"
