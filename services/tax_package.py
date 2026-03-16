# services/tax_package.py
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_

from core.extensions import db
from core.time import utcnow
from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    SafeToSpendSettings,
    TaxBufferLedger,
    Transaction,
    UserBankAccount,
)
from services.bank_accounts import display_name as bank_account_display_name
from services.evidence_vault import resolve_file_path
from services.onboarding import tax_profile_summary
from services.plan import PlanPermissionError, ensure_can_download_package

KST = ZoneInfo("Asia/Seoul")
SENSITIVE_ATTACHMENT_NAME_KEYWORDS = (
    "주민등록",
    "주민번호",
    "신분증",
    "가족관계",
    "등본",
    "초본",
    "여권",
    "운전면허",
    "idcard",
    "passport",
    "familyregister",
    "resident",
)
INVALID_FILENAME_CHAR_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
WHITESPACE_RE = re.compile(r"\s+")
LONG_DIGIT_RE = re.compile(r"\d{7,}")
NON_FILENAME_TEXT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")

MIME_EXTENSION_MAP: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


# -----------------------------
# Month utils
# -----------------------------
def _month_range_kst_naive(month_key: str) -> tuple[datetime, datetime]:
    """month_key(YYYY-MM)의 '한국시간 월 경계'를 naive datetime 범위로 반환."""
    y, m = month_key.split("-")
    y = int(y)
    m = int(m)

    start = datetime(y, m, 1, 0, 0, 0)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0)
    return start, end


def _normalize_month_key(month_key: str | None) -> str:
    raw = str(month_key or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", raw):
        return utcnow().strftime("%Y-%m")
    try:
        datetime.strptime(f"{raw}-01", "%Y-%m-%d")
    except Exception:
        return utcnow().strftime("%Y-%m")
    return raw


def _normalize_account_filter(account_filter: str | None, account_id: int | None = None) -> tuple[str, int]:
    raw = str(account_filter or "all").strip().lower()
    if raw in ("", "all"):
        return "all", 0
    if raw == "unassigned":
        return "unassigned", 0
    aid = 0
    if account_id is not None:
        try:
            aid = int(account_id)
        except Exception:
            aid = 0
    if aid <= 0:
        try:
            aid = int(raw)
        except Exception:
            aid = 0
    if aid <= 0:
        return "all", 0
    return str(aid), int(aid)


def _apply_transaction_account_filter(query, account_filter: str, account_id: int):
    if account_filter == "unassigned":
        return query.filter(Transaction.bank_account_id.is_(None))
    if int(account_id or 0) > 0:
        return query.filter(Transaction.bank_account_id == int(account_id))
    return query


def _ensure_settings(user_pk: int) -> SafeToSpendSettings:
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()
    return s


def _tax_rate(s: SafeToSpendSettings) -> float:
    r = float(getattr(s, "default_tax_rate", 0.15) or 0.15)
    if r > 1:
        r = r / 100.0
    return max(0.0, min(r, 0.95))


def _resolve_tax_buffer_metrics(
    *,
    user_pk: int,
    month_key: str,
    fallback_income_included_total: int,
) -> dict[str, Any]:
    try:
        # local import to avoid circular dependency during module import
        from services.risk import compute_tax_estimate

        est = compute_tax_estimate(user_pk=int(user_pk), month_key=str(month_key))
        return {
            "tax_rate": float(getattr(est, "tax_rate", 0.0) or 0.0),
            "tax_buffer_total": int(getattr(est, "buffer_total_krw", 0) or 0),
            "tax_buffer_target": int(getattr(est, "buffer_target_krw", 0) or 0),
            "tax_buffer_shortage": int(getattr(est, "buffer_shortage_krw", 0) or 0),
            "tax_due_est_krw": int(getattr(est, "tax_due_est_krw", 0) or 0),
            "tax_calculation_mode": str(getattr(est, "tax_calculation_mode", "unknown") or "unknown"),
            "official_calculable": bool(getattr(est, "official_calculable", False)),
            "is_limited_estimate": bool(getattr(est, "is_limited_estimate", False)),
            "official_block_reason": str(getattr(est, "official_block_reason", "") or ""),
            "taxable_income_input_source": str(getattr(est, "taxable_income_input_source", "missing") or "missing"),
        }
    except Exception:
        s = _ensure_settings(int(user_pk))
        rate = _tax_rate(s)
        tax_buffer_total = (
            db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
            .filter(TaxBufferLedger.user_pk == int(user_pk))
            .scalar()
        ) or 0
        tax_buffer_target = int(int(fallback_income_included_total or 0) * float(rate))
        tax_buffer_shortage = max(0, int(tax_buffer_target) - int(tax_buffer_total))
        return {
            "tax_rate": float(rate),
            "tax_buffer_total": int(tax_buffer_total),
            "tax_buffer_target": int(tax_buffer_target),
            "tax_buffer_shortage": int(tax_buffer_shortage),
            "tax_due_est_krw": int(tax_buffer_target),
            "tax_calculation_mode": "legacy_rate_fallback",
            "official_calculable": False,
            "is_limited_estimate": True,
            "official_block_reason": "tax_estimate_unavailable",
            "taxable_income_input_source": "fallback_income_rate",
        }


def _krw(n: int) -> str:
    return f"{int(n or 0):,}원"


def _to_kst(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
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


def _is_sensitive_attachment_name(filename: str | None) -> bool:
    text = str(filename or "").strip().lower()
    if not text:
        return False
    for token in SENSITIVE_ATTACHMENT_NAME_KEYWORDS:
        if str(token).strip().lower() in text:
            return True
    return False


def _extract_attachment_extension(original_filename: str | None, mime_type: str | None) -> str:
    text = str(original_filename or "").strip()
    ext = ""
    if "." in text:
        ext = "." + str(text.rsplit(".", 1)[-1]).strip().lower()
        ext = re.sub(r"[^a-z0-9]", "", ext.lstrip("."))
        ext = f".{ext}" if ext else ""
    if not ext:
        ext = str(MIME_EXTENSION_MAP.get(str(mime_type or "").strip().lower()) or "")
    if not ext:
        ext = ".bin"
    if len(ext) > 12:
        return ".bin"
    return ext


def _normalize_filename_token(
    value: str | None,
    *,
    fallback: str,
    max_len: int = 24,
    strip_long_digits: bool = False,
) -> str:
    text = str(value or "").strip()
    text = INVALID_FILENAME_CHAR_RE.sub(" ", text)
    if bool(strip_long_digits):
        text = LONG_DIGIT_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    text = NON_FILENAME_TEXT_RE.sub("", text)
    if not text:
        text = str(fallback).strip()
    text = WHITESPACE_RE.sub("", text)
    if len(text) > int(max_len):
        text = text[: int(max_len)]
    return text or str(fallback).strip()


def _format_export_datetime_tokens(occurred_at: datetime | None) -> tuple[str, str]:
    if isinstance(occurred_at, datetime):
        dtk = _to_kst(occurred_at)
        if dtk:
            return dtk.strftime("%Y%m%d"), dtk.strftime("%H%M%S")
    now_kst = _to_kst(utcnow()) or utcnow()
    return now_kst.strftime("%Y%m%d"), "시간미상"


def _guess_evidence_kind(ev: EvidenceItem | None) -> str:
    text = " ".join(
        [
            str(getattr(ev, "original_filename", "") or ""),
            str(getattr(ev, "mime_type", "") or ""),
            str(getattr(ev, "note", "") or ""),
        ]
    ).lower()
    if ("전자" in text and ("영수증" in text or "receipt" in text)) or ("카카오" in text and "톡" in text):
        return "전자영수증"
    if "receipt" in text or "영수증" in text:
        return "영수증"
    if "attachment" in text or "첨부" in text:
        return "첨부파일"
    return "증빙"


def _build_attachment_export_filename(
    *,
    tx: Transaction | Any,
    ev: EvidenceItem | Any,
    sequence: int = 1,
) -> str:
    date_token, time_token = _format_export_datetime_tokens(getattr(tx, "occurred_at", None))
    amount_raw = _safe_int(getattr(tx, "amount_krw", 0), 0)
    amount_token = f"{amount_raw}원" if amount_raw > 0 else "금액미상"

    counterparty_text = str(getattr(tx, "counterparty", "") or "").strip()
    memo_text = str(getattr(tx, "memo", "") or "").strip()
    counterparty_source = counterparty_text or memo_text or "거래처미상"
    kind_source = _guess_evidence_kind(ev) or "증빙"
    seq = max(1, int(sequence or 1))
    seq_token = f"{seq:03d}"

    date_token = _normalize_filename_token(date_token, fallback="날짜미상", max_len=8)
    time_token = _normalize_filename_token(time_token, fallback="시간미상", max_len=8)
    amount_token = _normalize_filename_token(amount_token, fallback="금액미상", max_len=20)
    counterparty_token = _normalize_filename_token(
        counterparty_source,
        fallback="거래처미상",
        max_len=24,
        strip_long_digits=True,
    )
    kind_token = _normalize_filename_token(kind_source, fallback="증빙", max_len=12)

    ext = _extract_attachment_extension(
        getattr(ev, "original_filename", None),
        getattr(ev, "mime_type", None),
    )
    return f"{date_token}_{time_token}_{amount_token}_{counterparty_token}_{kind_token}_{seq_token}{ext}"


def _build_attachment_zip_path(
    *,
    tx: Transaction | Any,
    ev: EvidenceItem | Any,
    sequence_by_tx: dict[int, int],
    name_registry: set[str],
    attachments_dir: str = "03_증빙첨부(attachments)/attachments",
) -> str:
    tx_id = int(getattr(tx, "id", 0) or 0)
    seq = int(sequence_by_tx.get(tx_id, 0) or 0) + 1
    base = _build_attachment_export_filename(tx=tx, ev=ev, sequence=seq)
    while base in name_registry:
        seq += 1
        base = _build_attachment_export_filename(tx=tx, ev=ev, sequence=seq)
    sequence_by_tx[tx_id] = seq
    name_registry.add(base)
    return f"{attachments_dir}/{base}"


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


@dataclass(frozen=True)
class PreflightCheck:
    code: str
    title: str
    level: str  # fail/warn
    status: str  # pass/fail/warn
    metric: str
    target: str
    message: str


def _evidence_defaults_from_expense_status(expense_status: str | None) -> tuple[str, str]:
    # business: 필수+누락, personal: 불필요, 나머지: 검토+누락
    if expense_status == "business":
        return "required", "missing"
    if expense_status == "personal":
        return "not_needed", "not_needed"
    return "maybe", "missing"


def _ensure_month_evidence_items(user_pk: int, start_dt: datetime, end_dt: datetime) -> int:
    """월 범위 내 지출(out) 거래에 EvidenceItem이 없으면 생성."""
    rows = (
        db.session.query(Transaction.id, ExpenseLabel.status, EvidenceItem.id)
        .select_from(Transaction)
        .outerjoin(
            ExpenseLabel,
            and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
        )
        .outerjoin(
            EvidenceItem,
            and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )

    created = 0
    for tx_id, exp_status, ev_id in rows:
        if ev_id is not None:
            continue
        req, st = _evidence_defaults_from_expense_status(exp_status)
        db.session.add(EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement=req, status=st, note=None))
        created += 1

    if created:
        db.session.commit()
    return created


# preview용 (이전 이름 호환)
def _ensure_month_evidence_rows_for_pkg(user_pk: int, start_dt: datetime, end_dt: datetime) -> int:
    return _ensure_month_evidence_items(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)


def _parse_kst_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def _build_duplicate_suspects(tx_records: list[dict[str, Any]], *, max_days: int = 3) -> list[dict[str, Any]]:
    """
    동일 금액/거래처/구분 + 거래일 근접(기본 3일)인 중복 의심 거래를 뽑는다.
    신고 차질을 막기 위한 사전 점검용이며, 최종 중복 확정이 아니다.
    """
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for r in tx_records:
        cp = (r.get("counterparty") or "").strip().lower()
        key = (str(r.get("direction") or ""), _safe_int(r.get("amount_krw")), cp)
        grouped.setdefault(key, []).append(r)

    suspects: list[dict[str, Any]] = []
    for (direction, amount, cp), rows in grouped.items():
        if len(rows) < 2:
            continue
        rows_sorted = sorted(
            rows,
            key=lambda x: _parse_kst_dt(x.get("occurred_at_kst")) or datetime.min,
        )
        for i in range(1, len(rows_sorted)):
            a = rows_sorted[i - 1]
            b = rows_sorted[i]
            adt = _parse_kst_dt(a.get("occurred_at_kst"))
            bdt = _parse_kst_dt(b.get("occurred_at_kst"))
            if not adt or not bdt:
                continue
            if abs((bdt - adt).days) > max_days:
                continue
            suspects.append(
                {
                    "direction": direction,
                    "amount_krw": amount,
                    "counterparty": a.get("counterparty") or b.get("counterparty") or "",
                    "tx_id_a": a.get("tx_id"),
                    "date_a": a.get("date_kst") or "",
                    "tx_id_b": b.get("tx_id"),
                    "date_b": b.get("date_kst") or "",
                    "distance_days": abs((bdt - adt).days),
                    "why": "동일 금액/거래처/구분 + 근접 일자",
                }
            )
    suspects.sort(key=lambda x: (_safe_int(x.get("amount_krw"), 0), -_safe_int(x.get("distance_days"), 0)), reverse=True)
    return suspects


def _has_attached_evidence(record: dict[str, Any]) -> bool:
    status = str(record.get("evidence_status") or "").strip().lower()
    if status == "attached":
        return True
    return bool(record.get("evidence_has_file"))


def _build_validation_report(
    tx_records: list[dict[str, Any]],
    duplicate_suspects: list[dict[str, Any]],
    *,
    top_n: int = 5,
) -> dict[str, Any]:
    rows = [r for r in tx_records if isinstance(r, dict)]
    required_rows = [
        r
        for r in rows
        if str(r.get("direction") or "") == "out"
        and str(r.get("evidence_requirement") or "") in {"required", "maybe"}
    ]
    required_total = int(len(required_rows))
    attached_total = int(sum(1 for r in required_rows if _has_attached_evidence(r)))
    remaining_total = max(0, required_total - attached_total)
    completion_rate_pct = round((attached_total * 100.0 / required_total), 1) if required_total > 0 else None

    missing_candidates = [
        r
        for r in required_rows
        if str(r.get("evidence_status") or "").strip().lower() == "missing"
        and not _has_attached_evidence(r)
    ]
    missing_candidates.sort(
        key=lambda r: (
            0 if str(r.get("evidence_requirement") or "") == "required" else 1,
            -_safe_int(r.get("amount_krw"), 0),
            str(r.get("date_kst") or ""),
        )
    )
    missing_top = [
        {
            "tx_id": int(_safe_int(r.get("tx_id"), 0)),
            "date_kst": str(r.get("date_kst") or ""),
            "counterparty": str(r.get("counterparty") or ""),
            "amount_krw": int(_safe_int(r.get("amount_krw"), 0)),
            "requirement": str(r.get("evidence_requirement") or ""),
            "status_label": "필수 누락" if str(r.get("evidence_requirement") or "") == "required" else "확인 필요",
            "reason": "증빙이 필요한 거래인데 아직 첨부되지 않았어요.",
        }
        for r in missing_candidates[: max(1, int(top_n))]
    ]

    duplicate_top = [
        {
            "amount_krw": int(_safe_int(row.get("amount_krw"), 0)),
            "counterparty": str(row.get("counterparty") or ""),
            "date_a": str(row.get("date_a") or ""),
            "date_b": str(row.get("date_b") or ""),
            "distance_days": int(_safe_int(row.get("distance_days"), 0)),
            "why": "동일 금액/거래처/구분 + 근접 일자",
        }
        for row in list(duplicate_suspects or [])[: max(1, int(top_n))]
    ]

    abs_values = [abs(_safe_int(r.get("amount_krw"), 0)) for r in rows if _safe_int(r.get("amount_krw"), 0) > 0]
    avg_amount = float(sum(abs_values) / len(abs_values)) if abs_values else 0.0
    outlier_top: list[dict[str, Any]] = []
    if avg_amount > 0:
        outlier_candidates = []
        for r in rows:
            amt = abs(_safe_int(r.get("amount_krw"), 0))
            if amt <= 0:
                continue
            ratio = float(amt / avg_amount)
            if ratio < 2.0:
                continue
            outlier_candidates.append(
                {
                    "tx_id": int(_safe_int(r.get("tx_id"), 0)),
                    "date_kst": str(r.get("date_kst") or ""),
                    "counterparty": str(r.get("counterparty") or ""),
                    "direction": str(r.get("direction") or ""),
                    "amount_krw": int(amt),
                    "ratio_vs_avg": round(ratio, 1),
                    "why": f"월 평균 대비 {ratio:.1f}배",
                }
            )
        outlier_candidates.sort(key=lambda row: (-float(row.get("ratio_vs_avg") or 0.0), -int(row.get("amount_krw") or 0)))
        outlier_top = outlier_candidates[: max(1, int(top_n))]

    return {
        "summary": {
            "transaction_total": int(len(rows)),
            "evidence_required_total": required_total,
            "evidence_attached_total": attached_total,
            "remaining_total": remaining_total,
            "completion_rate_pct": completion_rate_pct,
        },
        "missing_top": missing_top,
        "duplicate_top": duplicate_top,
        "outlier_top": outlier_top,
    }


def _evaluate_preflight(
    *,
    month_key: str,
    stats: PackageStats,
    required_total_count: int,
    required_attached_count: int,
    mixed_count: int,
    mixed_note_missing_count: int,
    duplicate_suspects: list[dict[str, Any]],
) -> dict[str, Any]:
    required_rate = 100.0 if required_total_count <= 0 else (required_attached_count * 100.0 / required_total_count)
    checks: list[PreflightCheck] = []

    checks.append(
        PreflightCheck(
            code="income_unknown_zero",
            title="수입 미분류 0건",
            level="fail",
            status=("pass" if stats.income_unknown_count == 0 else "fail"),
            metric=f"{stats.income_unknown_count}건",
            target="0건",
            message=("통과" if stats.income_unknown_count == 0 else "미분류 수입을 먼저 확정해야 합니다."),
        )
    )

    checks.append(
        PreflightCheck(
            code="required_missing_zero",
            title="필수 증빙 누락 0건",
            level="fail",
            status=("pass" if stats.evidence_missing_required_count == 0 else "fail"),
            metric=f"{stats.evidence_missing_required_count}건",
            target="0건",
            message=("통과" if stats.evidence_missing_required_count == 0 else "필수 증빙 누락을 먼저 해소해야 합니다."),
        )
    )

    checks.append(
        PreflightCheck(
            code="required_attachment_rate",
            title="필수 증빙 첨부율",
            level="warn",
            status=("pass" if required_rate >= 100.0 else "warn"),
            metric=f"{required_rate:.1f}%",
            target="100.0%",
            message=("통과" if required_rate >= 100.0 else "필수 증빙 첨부율이 100%가 아닙니다."),
        )
    )

    checks.append(
        PreflightCheck(
            code="mixed_note_ready",
            title="혼합 지출 근거 메모",
            level="warn",
            status=("pass" if mixed_note_missing_count == 0 else "warn"),
            metric=f"{mixed_note_missing_count}건 / 혼합 {mixed_count}건",
            target="누락 0건",
            message=("통과" if mixed_note_missing_count == 0 else "혼합 지출 안분 근거 메모를 보강하세요."),
        )
    )

    non_income_ratio = 0.0
    if stats.income_included_total > 0:
        non_income_ratio = stats.income_excluded_non_income_total / float(stats.income_included_total)
    checks.append(
        PreflightCheck(
            code="non_income_large_review",
            title="비수입 제외 금액 점검",
            level="warn",
            status=("pass" if non_income_ratio < 0.30 else "warn"),
            metric=f"{non_income_ratio * 100:.1f}%",
            target="< 30.0%",
            message=("통과" if non_income_ratio < 0.30 else "비수입 제외 비중이 커서 사유 확인이 필요합니다."),
        )
    )

    checks.append(
        PreflightCheck(
            code="duplicate_suspects_review",
            title="중복 의심 거래",
            level="warn",
            status=("pass" if len(duplicate_suspects) == 0 else "warn"),
            metric=f"{len(duplicate_suspects)}건",
            target="0건",
            message=("통과" if len(duplicate_suspects) == 0 else "중복 의심 거래를 확인하세요."),
        )
    )

    fail_count = sum(1 for c in checks if c.status == "fail")
    warn_count = sum(1 for c in checks if c.status == "warn")
    if fail_count > 0:
        status = "fail"
        verdict = "추가 보완 필요(다운로드 전 필수 항목 해결)"
    elif warn_count > 0:
        status = "warn"
        verdict = "진행 가능(권장 보완 항목 있음)"
    else:
        status = "pass"
        verdict = "신고 진행 가능"

    action_focus_by_code = {
        "income_unknown_zero": "income_confirm",
        "required_missing_zero": "receipt_required",
        "required_attachment_rate": "receipt_required",
        "mixed_note_ready": "expense_confirm",
        "non_income_large_review": "income_confirm",
        "duplicate_suspects_review": "receipt_attach",
    }
    action_text_by_code = {
        "income_unknown_zero": "수입 확정하기",
        "required_missing_zero": "필수 증빙 정리",
        "required_attachment_rate": "필수 증빙 보완",
        "mixed_note_ready": "혼합 지출 확인",
        "non_income_large_review": "비수입 제외 확인",
        "duplicate_suspects_review": "중복 의심 확인",
    }

    issue_priority = {
        "required_missing_zero": 10,
        "income_unknown_zero": 20,
        "required_attachment_rate": 30,
        "mixed_note_ready": 40,
        "non_income_large_review": 50,
        "duplicate_suspects_review": 60,
    }

    open_issues = [c for c in checks if c.status in {"fail", "warn"}]
    open_issues.sort(
        key=lambda c: (
            0 if c.status == "fail" else 1,
            issue_priority.get(c.code, 999),
        )
    )

    top_issues: list[dict[str, Any]] = []
    for c in open_issues[:3]:
        focus = action_focus_by_code.get(c.code, "receipt_required")
        top_issues.append(
            {
                "code": c.code,
                "severity": ("FAIL" if c.status == "fail" else "WARN"),
                "title": c.title,
                "message": c.message,
                "metric": c.metric,
                "target": c.target,
                "action_text": action_text_by_code.get(c.code, "지금 정리"),
                "action_url": f"/dashboard/review?month={month_key}&focus={focus}",
            }
        )

    return {
        "status": status,
        "verdict": verdict,
        "fail_count": fail_count,
        "warn_count": warn_count,
        "required_total_count": int(required_total_count),
        "required_attached_count": int(required_attached_count),
        "required_attachment_rate_pct": round(required_rate, 1),
        "mixed_total_count": int(mixed_count),
        "mixed_note_missing_count": int(mixed_note_missing_count),
        "checks": [c.__dict__ for c in checks],
        "duplicate_suspects_count": int(len(duplicate_suspects)),
        "top_issues": top_issues,
    }


def build_tax_package_preview(
    user_pk: int,
    month_key: str,
    account_filter: str | None = "all",
    account_id: int | None = None,
) -> dict[str, Any]:
    """ZIP 미리보기(구성/카운트)만 반환."""
    month_key = _normalize_month_key(month_key)
    account_filter_value, account_filter_id = _normalize_account_filter(account_filter, account_id)

    start_dt, end_dt = _month_range_kst_naive(month_key)

    # 패키지 신뢰성: 지출(out)에 EvidenceItem이 없으면 생성
    _ensure_month_evidence_rows_for_pkg(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    tx_total_query = (
        db.session.query(func.count(Transaction.id))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    tx_total = _apply_transaction_account_filter(tx_total_query, account_filter_value, account_filter_id).scalar() or 0

    in_agg_query = (
        db.session.query(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount_krw), 0),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    in_agg = _apply_transaction_account_filter(in_agg_query, account_filter_value, account_filter_id).first()
    out_agg_query = (
        db.session.query(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount_krw), 0),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    out_agg = _apply_transaction_account_filter(out_agg_query, account_filter_value, account_filter_id).first()
    tx_in_count = int(in_agg[0] or 0) if in_agg else 0
    tx_out_count = int(out_agg[0] or 0) if out_agg else 0

    miss_rows_query = (
        db.session.query(
            EvidenceItem.requirement,
            func.count(EvidenceItem.id),
            func.coalesce(func.sum(Transaction.amount_krw), 0),
        )
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement.in_(("required", "maybe")))
        .group_by(EvidenceItem.requirement)
    )
    miss_rows = _apply_transaction_account_filter(miss_rows_query, account_filter_value, account_filter_id).all()

    missing_required = 0
    missing_maybe = 0
    for req, cnt, _amt in miss_rows:
        if req == "required":
            missing_required = int(cnt or 0)
        elif req == "maybe":
            missing_maybe = int(cnt or 0)

    missing_total = int(missing_required + missing_maybe)

    evidence_index_count_query = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    evidence_index_count = (
        _apply_transaction_account_filter(evidence_index_count_query, account_filter_value, account_filter_id).scalar()
        or 0
    )

    attachments_count_query = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.file_key.isnot(None))
        .filter(EvidenceItem.deleted_at.is_(None))
    )
    attachments_count = (
        _apply_transaction_account_filter(attachments_count_query, account_filter_value, account_filter_id).scalar()
        or 0
    )

    income_rows_query = (
        db.session.query(Transaction.amount_krw, IncomeLabel.status)
        .select_from(Transaction)
        .outerjoin(
            IncomeLabel,
            and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    income_rows = _apply_transaction_account_filter(income_rows_query, account_filter_value, account_filter_id).all()
    income_included_total = 0
    income_excluded_non_income_total = 0
    income_unknown_count = 0
    for amount, status in income_rows:
        amt = _safe_int(amount)
        if status == "non_income":
            income_excluded_non_income_total += amt
        else:
            income_included_total += amt
            if not status or status == "unknown":
                income_unknown_count += 1

    required_total_count_query = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "required")
    )
    required_total_count = (
        _apply_transaction_account_filter(required_total_count_query, account_filter_value, account_filter_id).scalar()
        or 0
    )

    required_attached_count_query = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "required")
        .filter(or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)))
    )
    required_attached_count = (
        _apply_transaction_account_filter(required_attached_count_query, account_filter_value, account_filter_id).scalar()
        or 0
    )

    mixed_rows_query = (
        db.session.query(ExpenseLabel.status, EvidenceItem.note)
        .select_from(Transaction)
        .join(
            ExpenseLabel,
            and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
        )
        .outerjoin(
            EvidenceItem,
            and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(ExpenseLabel.status == "mixed")
    )
    mixed_rows = _apply_transaction_account_filter(mixed_rows_query, account_filter_value, account_filter_id).all()
    mixed_count = len(mixed_rows)
    mixed_note_missing_count = sum(1 for _st, note in mixed_rows if not (note or "").strip())

    tx_rows_query = (
        db.session.query(Transaction, ExpenseLabel, EvidenceItem)
        .select_from(Transaction)
        .outerjoin(
            ExpenseLabel,
            and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
        )
        .outerjoin(
            EvidenceItem,
            and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    )
    tx_rows = _apply_transaction_account_filter(tx_rows_query, account_filter_value, account_filter_id).all()
    preview_tx_records = []
    for tx, exp_label, evidence in tx_rows:
        preview_tx_records.append(
            {
                "tx_id": int(tx.id),
                "occurred_at_kst": _fmt_kst(tx.occurred_at, "%Y-%m-%d %H:%M"),
                "date_kst": _fmt_kst(tx.occurred_at, "%Y-%m-%d"),
                "direction": str(tx.direction or ""),
                "amount_krw": int(_safe_int(tx.amount_krw)),
                "counterparty": str(tx.counterparty or tx.memo or ""),
                "expense_label_status": str(exp_label.status or "") if exp_label else "",
                "evidence_requirement": str(evidence.requirement or "") if evidence else "",
                "evidence_status": str(evidence.status or "") if evidence else "",
                "evidence_has_file": bool(evidence and evidence.file_key and not evidence.deleted_at),
            }
        )
    duplicate_suspects = _build_duplicate_suspects(preview_tx_records)
    validation_report = _build_validation_report(preview_tx_records, duplicate_suspects, top_n=5)

    tax_metrics = _resolve_tax_buffer_metrics(
        user_pk=int(user_pk),
        month_key=month_key,
        fallback_income_included_total=int(income_included_total),
    )

    dummy_stats = PackageStats(
        month_key=month_key,
        period_start_kst=start_dt.strftime("%Y-%m-%d"),
        period_end_kst=(end_dt - datetime.resolution).strftime("%Y-%m-%d"),
        generated_at_kst=_fmt_kst(utcnow(), "%Y-%m-%d %H:%M") or utcnow().strftime("%Y-%m-%d %H:%M"),
        tx_total=int(tx_total),
        tx_in_count=int(tx_in_count),
        tx_out_count=int(tx_out_count),
        sum_in_total=int(in_agg[1] or 0) if in_agg else 0,
        sum_out_total=int(out_agg[1] or 0) if out_agg else 0,
        income_included_total=int(income_included_total),
        income_excluded_non_income_total=int(income_excluded_non_income_total),
        income_unknown_count=int(income_unknown_count),
        expense_business_total=0,
        expense_personal_total=0,
        expense_mixed_total=0,
        expense_unknown_total=0,
        evidence_missing_required_count=int(missing_required),
        evidence_missing_required_amount=0,
        evidence_missing_maybe_count=int(missing_maybe),
        evidence_missing_maybe_amount=0,
        tax_rate=float(tax_metrics.get("tax_rate") or 0.0),
        tax_buffer_total=int(tax_metrics.get("tax_buffer_total") or 0),
        tax_buffer_target=int(tax_metrics.get("tax_buffer_target") or 0),
        tax_buffer_shortage=int(tax_metrics.get("tax_buffer_shortage") or 0),
    )
    preflight = _evaluate_preflight(
        month_key=month_key,
        stats=dummy_stats,
        required_total_count=int(required_total_count),
        required_attached_count=int(required_attached_count),
        mixed_count=int(mixed_count),
        mixed_note_missing_count=int(mixed_note_missing_count),
        duplicate_suspects=duplicate_suspects,
    )

    root = f"SafeToSpend_TaxPackage_{month_key}"

    return {
        "month_key": month_key,
        "root": root,
        "counts": {
            "tx_total": int(tx_total),
            "tx_in_count": int(tx_in_count),
            "tx_out_count": int(tx_out_count),
            "missing_required": int(missing_required),
            "missing_maybe": int(missing_maybe),
            "missing_total": int(missing_total),
            "evidence_index_count": int(evidence_index_count),
            "attachments_count": int(attachments_count),
        },
        "preflight": {
            **preflight,
            "duplicate_suspects_preview": duplicate_suspects[:20],
        },
        "validation_report": validation_report,
        "tax_estimate": {
            "tax_rate": float(tax_metrics.get("tax_rate") or 0.0),
            "tax_due_est_krw": int(tax_metrics.get("tax_due_est_krw") or 0),
            "tax_buffer_total": int(tax_metrics.get("tax_buffer_total") or 0),
            "tax_buffer_target": int(tax_metrics.get("tax_buffer_target") or 0),
            "tax_buffer_shortage": int(tax_metrics.get("tax_buffer_shortage") or 0),
            "tax_calculation_mode": str(tax_metrics.get("tax_calculation_mode") or "unknown"),
            "official_calculable": bool(tax_metrics.get("official_calculable")),
            "is_limited_estimate": bool(tax_metrics.get("is_limited_estimate")),
            "official_block_reason": str(tax_metrics.get("official_block_reason") or ""),
            "taxable_income_input_source": str(tax_metrics.get("taxable_income_input_source") or "missing"),
        },
        "files": [
            {"name": "00_세무사_요약/README_세무사용.txt", "desc": "세무사 1분 판단용 안내"},
            {"name": "00_세무사_요약/manifest.json", "desc": "패키지 구성/집계/품질 메타"},
            {"name": "00_세무사_요약/품질리포트.xlsx", "desc": "자동 사전점검(통과/보완)"},
            {"name": "00_세무사_요약/profile_summary.txt", "desc": "사용자 입력 프로필 요약"},
            {"name": "00_세무사_요약/검증리포트.txt", "desc": "누락/중복/이상치 검증 요약"},
            {"name": "01_정리표/세무사용_정리표.xlsx", "desc": "요약/거래/누락/증빙"},
            {"name": "01_정리표/서류체크리스트.xlsx", "desc": "거래 외 서류 체크리스트"},
            {"name": "02_원장_원본데이터(raw)/transactions.xlsx", "desc": "원본 거래 원장"},
            {"name": "02_원장_원본데이터(raw)/evidence_index.xlsx", "desc": "원본 증빙 인덱스"},
            {"name": "02_원장_원본데이터(raw)/missing_evidence.xlsx", "desc": "원본 누락 리스트"},
            {"name": "03_증빙첨부(attachments)/attachments_index.xlsx", "desc": "거래↔첨부 매핑표"},
            {"name": "03_증빙첨부(attachments)/attachments/", "desc": "실제 증빙 파일"},
            {"name": "04_홈택스_및_연간필수자료(사용자추가)/", "desc": "연간 필수 자료 슬롯"},
            {"name": "05_추가서류(사용자추가)/", "desc": "공제/기본/기타 추가 자료 슬롯"},
        ],
    }


# -----------------------------
# Public API
# -----------------------------
def build_tax_package_zip(
    user_pk: int,
    month_key: str,
    account_filter: str | None = "all",
    account_id: int | None = None,
) -> tuple[io.BytesIO, str]:
    """
    세무사 전달 패키지(zip)

    ZIP 구조:
    - SafeToSpend_TaxPackage_<YYYY-MM>/
      - 00_세무사_요약/...
      - 01_정리표/...
      - 02_원장_원본데이터(raw)/...
      - 03_증빙첨부(attachments)/...
      - 04_홈택스_및_연간필수자료(사용자추가)/...
      - 05_추가서류(사용자추가)/...
    """
    try:
        ensure_can_download_package(int(user_pk))
    except PlanPermissionError:
        # 라우트 가드가 있어도 서비스 단의 직접 호출 우회를 막는다.
        raise

    month_key = _normalize_month_key(month_key)
    account_filter_value, account_filter_id = _normalize_account_filter(account_filter, account_id)

    start_dt, end_dt = _month_range_kst_naive(month_key)

    # 신뢰성: 지출(out)에 EvidenceItem이 누락되면 생성
    _ensure_month_evidence_items(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    period_start_kst = start_dt.strftime("%Y-%m-%d")
    try:
        last_day = (end_dt - datetime.resolution).date()
        period_end_kst = last_day.strftime("%Y-%m-%d")
    except Exception:
        period_end_kst = ""

    rows_query = (
        db.session.query(Transaction, IncomeLabel, ExpenseLabel, EvidenceItem)
        .outerjoin(IncomeLabel, and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk))
        .outerjoin(ExpenseLabel, and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk))
        .outerjoin(EvidenceItem, and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    )
    rows = _apply_transaction_account_filter(rows_query, account_filter_value, account_filter_id).all()
    bank_account_ids = {
        int(getattr(tx, "bank_account_id", 0) or 0)
        for tx, _il, _el, _ev in rows
        if int(getattr(tx, "bank_account_id", 0) or 0) > 0
    }
    bank_account_map: dict[int, str] = {}
    if bank_account_ids:
        bank_rows = (
            db.session.query(UserBankAccount)
            .filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.id.in_(list(bank_account_ids)))
            .all()
        )
        bank_account_map = {
            int(row.id): bank_account_display_name(row)
            for row in bank_rows
        }

    tx_records: list[dict[str, Any]] = []
    evidence_index_records: list[dict[str, Any]] = []
    attachments_index_records: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []  # {zip_path, abs_path}
    attachment_sequence_by_tx: dict[int, int] = {}
    attachment_name_registry: set[str] = set()

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
    required_total_count = 0
    required_attached_count = 0
    mixed_count = 0
    mixed_note_missing_count = 0

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

        if tx.direction == "in":
            if income_status == "non_income":
                income_excluded_non_income_total += amt
            else:
                income_included_total += amt
                if not income_status or income_status == "unknown":
                    income_unknown_count += 1

        if tx.direction == "out":
            if expense_status == "business":
                expense_business_total += amt
            elif expense_status == "personal":
                expense_personal_total += amt
            elif expense_status == "mixed":
                expense_mixed_total += amt
                mixed_count += 1
                if not (ev_note or "").strip():
                    mixed_note_missing_count += 1
            else:
                expense_unknown_total += amt

            if ev_status == "missing" and ev_req in ("required", "maybe"):
                if ev_req == "required":
                    ev_req_cnt += 1
                    ev_req_amt += amt
                else:
                    ev_maybe_cnt += 1
                    ev_maybe_amt += amt

            if ev_req == "required":
                required_total_count += 1
                if ev_status == "attached" or (ev and ev.file_key and (ev.deleted_at is None)):
                    required_attached_count += 1

        attachment_zip_path = ""
        if tx.direction == "out" and ev and ev.file_key and (ev.deleted_at is None) and (ev.status == "attached"):
            try:
                abs_path = resolve_file_path(ev.file_key)
                if abs_path.exists() and abs_path.is_file():
                    if _is_sensitive_attachment_name(ev.original_filename):
                        attachment_zip_path = ""
                    else:
                        attachment_zip_path = _build_attachment_zip_path(
                            tx=tx,
                            ev=ev,
                            sequence_by_tx=attachment_sequence_by_tx,
                            name_registry=attachment_name_registry,
                        )
                        attachments.append({"zip_path": attachment_zip_path, "abs_path": abs_path})
            except Exception:
                attachment_zip_path = ""

        tx_records.append(
            {
                "tx_id": tx.id,
                "occurred_at_kst": occurred_kst_str,
                "date_kst": date_kst_str,
                "direction": tx.direction,
                "amount_krw": amt,
                "bank_account": (
                    bank_account_map.get(int(tx.bank_account_id))
                    if int(getattr(tx, "bank_account_id", 0) or 0) > 0
                    else "미지정"
                ),
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

        if tx.direction == "out" and ev:
            attachments_index_records.append(
                {
                    "tx_id": tx.id,
                    "date_kst": date_kst_str,
                    "counterparty": tx.counterparty or "",
                    "amount_krw": amt,
                    "evidence_requirement": ev.requirement or "",
                    "evidence_status": ev.status or "",
                    "evidence_original_filename": ev.original_filename or "",
                    "attachment_zip_path": attachment_zip_path,
                }
            )
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

    tax_metrics = _resolve_tax_buffer_metrics(
        user_pk=int(user_pk),
        month_key=month_key,
        fallback_income_included_total=int(income_included_total),
    )
    rate = float(tax_metrics.get("tax_rate") or 0.0)
    tax_buffer_total = int(tax_metrics.get("tax_buffer_total") or 0)
    tax_buffer_target = int(tax_metrics.get("tax_buffer_target") or 0)
    tax_buffer_shortage = int(tax_metrics.get("tax_buffer_shortage") or 0)
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
    duplicate_suspects = _build_duplicate_suspects(tx_records)
    validation_report = _build_validation_report(tx_records, duplicate_suspects, top_n=5)
    preflight = _evaluate_preflight(
        month_key=month_key,
        stats=stats,
        required_total_count=int(required_total_count),
        required_attached_count=int(required_attached_count),
        mixed_count=int(mixed_count),
        mixed_note_missing_count=int(mixed_note_missing_count),
        duplicate_suspects=duplicate_suspects,
    )

    # ---- headers (raw keys)
    tx_header = [
        "tx_id",
        "occurred_at_kst",
        "date_kst",
        "direction",
        "amount_krw",
        "bank_account",
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
    miss_header = ["priority", "tx_id", "date_kst", "amount_krw", "counterparty", "memo", "requirement", "why", "next_action"]
    attachments_index_header = [
        "tx_id",
        "date_kst",
        "counterparty",
        "amount_krw",
        "evidence_requirement",
        "evidence_status",
        "evidence_original_filename",
        "attachment_zip_path",
    ]

    attachments_count = sum(1 for _a in attachments)

    checklist_rows = [
        {"group": "기본/인적", "required": "기본 제외", "item": "신분증/주민등록 관련 서류", "where": "별도 안전 채널", "status": "앱 업로드 제외", "note": "기본 플로우에서는 수집/저장하지 않아요."},
        {"group": "기본/인적", "required": "기본 제외", "item": "가족관계증명서(또는 등본)", "where": "별도 안전 채널", "status": "앱 업로드 제외", "note": "기본 플로우에서는 수집/저장하지 않아요."},
        {"group": "기본/인적", "required": "조건부", "item": "사업자등록증 사본", "where": "홈택스/보관 문서", "status": "미준비", "note": "사업자 있는 경우"},
        {"group": "기본/인적", "required": "조건부", "item": "홈택스 조회 권한(아이디/비번 또는 위임)", "where": "홈택스", "status": "미준비", "note": "세무사 확인 편의"},
        {"group": "기본/인적", "required": "조건부", "item": "지방세 납세증명/납부내역", "where": "정부24/위택스", "status": "미준비", "note": "지방세 납부 확인"},

        {"group": "매출(소득)", "required": "필수", "item": "사업용 계좌 입출금 내역(1년/월)", "where": "앱 자동 포함", "status": "자동 포함", "note": "01_정리표/세무사용_정리표.xlsx + 02_원장_원본데이터(raw)/transactions.xlsx"},
        {"group": "매출(소득)", "required": "조건부", "item": "매출처별 세금계산서/계산서", "where": "홈택스/거래처", "status": "미준비", "note": "누락 방지 체크"},
        {"group": "매출(소득)", "required": "조건부", "item": "카드매출/플랫폼 매출자료(쿠팡/네이버 등)", "where": "플랫폼 관리자", "status": "미준비", "note": "플랫폼 판매자/배달앱 등"},
        {"group": "매출(소득)", "required": "조건부", "item": "원천징수영수증(3.3%)", "where": "지급처", "status": "미준비", "note": "프리랜서 원천징수 있는 경우"},
        {"group": "매출(소득)", "required": "조건부", "item": "사업용 신용카드 매출/사용내역(홈택스 미등록 포함)", "where": "카드사/홈택스", "status": "미준비", "note": "카드 사용이 있으면 필요"},

        {"group": "비용(지출)", "required": "필수", "item": "지출 거래 + 증빙 상태/첨부", "where": "앱 자동 포함", "status": ("일부 포함" if attachments_count > 0 else "미첨부"), "note": f"03_증빙첨부(attachments)/attachments/ 첨부 {attachments_count}개 포함"},
        {"group": "비용(지출)", "required": "조건부", "item": "종이 세금계산서/수기 영수증/미반영 현금영수증", "where": "사용자 보관", "status": "미준비", "note": "홈택스 반영 누락분"},
        {"group": "비용(지출)", "required": "조건부", "item": "임대차계약서 + 임대료 입금증", "where": "계약서/이체내역", "status": "미준비", "note": "사무실/작업실 월세"},
        {"group": "비용(지출)", "required": "조건부", "item": "공과금 영수증(전기/전화/인터넷/가스 등)", "where": "고지서/납부내역", "status": "미준비", "note": "사업장 명의"},
        {"group": "비용(지출)", "required": "조건부", "item": "차량 관련 비용(보험/세금/수리비 등)", "where": "보험사/지자체/정비소", "status": "미준비", "note": "사업 사용 인정 범위 확인"},
        {"group": "비용(지출)", "required": "조건부", "item": "경조사비 증빙(청첩장/부고 등)", "where": "문자/초대장", "status": "미준비", "note": "접대비 한도/요건 있음"},
        {"group": "비용(지출)", "required": "조건부", "item": "기부금 영수증", "where": "단체 발급", "status": "미준비", "note": "교회/NGO 등"},

        {"group": "공제/감면", "required": "조건부", "item": "노란우산공제 납입증명", "where": "노란우산", "status": "미준비", "note": "해당자"},
        {"group": "공제/감면", "required": "조건부", "item": "연금저축/IRP 납입확인", "where": "금융기관", "status": "미준비", "note": "세액공제"},
        {"group": "공제/감면", "required": "조건부", "item": "건강보험료/국민연금 납부확인", "where": "공단", "status": "미준비", "note": "지역가입자 비용처리/공제"},
        {"group": "공제/감면", "required": "조건부", "item": "중소기업 특별세액감면 관련", "where": "세무사 확인", "status": "미준비", "note": "업종/지역 조건"},
    ]

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
        z.writestr(f"{root}/{rel_path}", buf.getvalue().encode("utf-8-sig"))

    def _wxlsx(
        rel_path: str,
        sheet_title: str,
        header: list[str],
        records: list[dict[str, Any]],
        *,
        table_prefix: str,
        apply_borders: bool,
    ) -> None:
        try:
            from openpyxl import Workbook
        except Exception:
            return

        from services.tax_package_excel_style import style_sheet, StyleConfig

        wb = Workbook()
        ws = wb.active
        ws.title = (sheet_title or "sheet")[:31]

        # header는 "키"로 넣고, style_sheet가 한글 라벨로 바꿈
        ws.append(header)
        for r in records:
            ws.append([r.get(h, "") for h in header])

        cfg = StyleConfig(
            apply_cell_borders=apply_borders,
            sample_rows_for_width=400,
            max_col_width=70.0,
        )
        style_sheet(ws, table_prefix=table_prefix, cfg=cfg)

        buf = io.BytesIO()
        wb.save(buf)
        z.writestr(f"{root}/{rel_path}", buf.getvalue())

    def _xlsx_bytes_make_summary_and_tables() -> bytes:
        """세무사용_정리표.xlsx 생성(요약/거래(보기)/거래(원본)/누락/증빙)"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment
        except Exception:
            return b""

        # ✅ 공통 스타일러(한글 헤더/테두리)
        from services.tax_package_excel_style import HEADER_KO, apply_table_borders

        table_seq = 0

        def _apply_table(
            ws,
            header: list[str],
            records: list[dict[str, Any]],
            col_specs: dict[str, dict[str, Any]] | None = None,
            table_name_prefix: str = "T",
        ) -> None:
            """표 영역: 내부 얇은 격자 + 외곽 두껍게 + 헤더 한글화 + 헤더 높이(깨짐 방지)"""
            nonlocal table_seq
            table_seq += 1

            from openpyxl.styles import Font, Alignment, PatternFill
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.table import Table, TableStyleInfo

            col_specs = col_specs or {}

            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill("solid", fgColor="111827")
            header_align = Alignment(vertical="center", horizontal="center", wrap_text=True)

            text_align = Alignment(vertical="top", wrap_text=False)
            wrap_align = Alignment(vertical="top", wrap_text=True)
            num_align = Alignment(vertical="center", horizontal="right")

            # header (키 -> 한글)
            display_header = [HEADER_KO.get(h, h) for h in header]
            ws.append(display_header)
            ws.row_dimensions[1].height = 34

            for c in range(1, len(header) + 1):
                cell = ws.cell(row=1, column=c)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align

            # body
            for r in records:
                ws.append([r.get(h, "") for h in header])

            nrows = max(1, len(records) + 1)
            last_col_letter = get_column_letter(len(header))

            ws.freeze_panes = "A2"
            ws.auto_filter.ref = f"A1:{last_col_letter}{nrows}"
            ws.sheet_view.showGridLines = False
            ws.sheet_view.zoomScale = 110

            # 폭 추정
            def _fmt_for_width(v: Any) -> str:
                if v is None:
                    return ""
                try:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return f"{int(v):,}"
                except Exception:
                    pass
                return str(v)[:80]

            maxlen = [len(str(HEADER_KO.get(h, h))) for h in header]
            scan_end = min(nrows, 1 + 600)
            for row_idx in range(2, scan_end + 1):
                for col_idx in range(1, len(header) + 1):
                    v = ws.cell(row=row_idx, column=col_idx).value
                    l = len(_fmt_for_width(v))
                    if l > maxlen[col_idx - 1]:
                        maxlen[col_idx - 1] = l

            from openpyxl.utils import get_column_letter as _gcl
            for col_idx, l in enumerate(maxlen, start=1):
                letter = _gcl(col_idx)
                ws.column_dimensions[letter].width = min(max(12, l + 5), 70)

            # column specs
            for col_name, spec in col_specs.items():
                if col_name not in header:
                    continue
                idx = header.index(col_name) + 1
                letter = _gcl(idx)

                if "width" in spec:
                    ws.column_dimensions[letter].width = spec["width"]

                if spec.get("wrap"):
                    for row_idx in range(2, nrows + 1):
                        ws.cell(row=row_idx, column=idx).alignment = wrap_align
                else:
                    for row_idx in range(2, nrows + 1):
                        ws.cell(row=row_idx, column=idx).alignment = text_align

                if "number_format" in spec:
                    fmt = spec["number_format"]
                    for row_idx in range(2, nrows + 1):
                        cell = ws.cell(row=row_idx, column=idx)
                        cell.number_format = fmt
                        cell.alignment = num_align

            # excel table
            safe_title = "".join([ch for ch in ws.title if ch.isalnum()]) or "Sheet"
            base_name = (table_name_prefix + safe_title)[:18]
            tbl_name = f"{base_name}{table_seq}"
            tbl = Table(displayName=tbl_name, ref=f"A1:{last_col_letter}{nrows}")
            tbl.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2",
                showFirstColumn=False,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False,
            )
            ws.add_table(tbl)

            # ✅ 표 영역에만: 내부 격자 + 외곽 두껍게
            apply_table_borders(
                ws,
                min_row=1,
                max_row=nrows,
                min_col=1,
                max_col=len(header),
                inner_grid=True,
            )

        wb = Workbook()
        wb.remove(wb.active)

        # 요약
        ws0 = wb.create_sheet("요약")
        ws0["A1"] = "SafeToSpend(쓸수있어) 세무사 전달 패키지"
        ws0["A1"].font = Font(bold=True, size=14)

        ws0["A3"] = "대상 월"; ws0["B3"] = stats.month_key
        ws0["A4"] = "기간(KST)"; ws0["B4"] = f"{stats.period_start_kst} ~ {stats.period_end_kst}"
        ws0["A5"] = "생성일(KST)"; ws0["B5"] = stats.generated_at_kst

        ws0["A7"] = "총 입금 합계"; ws0["B7"] = stats.sum_in_total
        ws0["A8"] = "총 출금 합계"; ws0["B8"] = stats.sum_out_total
        ws0["A9"] = "포함 수입(비수입 제외)"; ws0["B9"] = stats.income_included_total
        ws0["A10"] = "사업 경비(업무 확정)"; ws0["B10"] = stats.expense_business_total

        ws0["A12"] = "증빙 누락(필수)"; ws0["B12"] = stats.evidence_missing_required_count
        ws0["A13"] = "증빙 확인(검토)"; ws0["B13"] = stats.evidence_missing_maybe_count

        ws0["A15"] = "기본 세율"; ws0["B15"] = stats.tax_rate
        ws0["A16"] = "세금 금고(현재)"; ws0["B16"] = stats.tax_buffer_total
        ws0["A17"] = "세금 금고(목표)"; ws0["B17"] = stats.tax_buffer_target
        ws0["A18"] = "세금 금고(부족)"; ws0["B18"] = stats.tax_buffer_shortage

        ws0.column_dimensions["A"].width = 26
        ws0.column_dimensions["B"].width = 34
        for r in range(3, 19):
            ws0[f"A{r}"].font = Font(bold=True)
        for r in [7, 8, 9, 10, 16, 17, 18]:
            ws0[f"B{r}"].number_format = "#,##0"
        ws0.sheet_view.showGridLines = False
        ws0.sheet_view.zoomScale = 110

        # 거래(보기)
        ws1 = wb.create_sheet("거래")
        pretty_header = [
            "date_kst",
            "occurred_at_kst",
            "direction",
            "amount_krw",
            "bank_account",
            "counterparty",
            "memo",
            "income_label_status",
            "expense_label_status",
            "evidence_requirement",
            "evidence_status",
            "attachment_zip_path",
        ]
        _apply_table(
            ws1,
            pretty_header,
            tx_records,
            col_specs={
                "date_kst": {"width": 16},
                "occurred_at_kst": {"width": 26},
                "direction": {"width": 12},
                "amount_krw": {"width": 22, "number_format": "#,##0"},
                "bank_account": {"width": 24},
                "counterparty": {"width": 28},
                "memo": {"width": 70, "wrap": True},
                "income_label_status": {"width": 18},
                "expense_label_status": {"width": 18},
                "evidence_requirement": {"width": 18},
                "evidence_status": {"width": 16},
                "attachment_zip_path": {"width": 45},
            },
            table_name_prefix="TX",
        )

        # 거래(원본)
        ws1b = wb.create_sheet("거래_원본")
        _apply_table(
            ws1b,
            tx_header,
            tx_records,
            col_specs={
                "occurred_at_kst": {"width": 26},
                "date_kst": {"width": 16},
                "amount_krw": {"width": 22, "number_format": "#,##0"},
                "bank_account": {"width": 24},
                "counterparty": {"width": 28},
                "memo": {"width": 70, "wrap": True},
                "source": {"width": 14},
                "external_hash": {"width": 32},
                "evidence_original_filename": {"width": 60, "wrap": True},
                "attachment_zip_path": {"width": 45},
            },
            table_name_prefix="RAW",
        )

        # 누락
        ws2 = wb.create_sheet("누락")
        _apply_table(
            ws2,
            miss_header,
            missing_list[:200],
            col_specs={
                "priority": {"width": 10},
                "tx_id": {"width": 12},
                "date_kst": {"width": 16},
                "amount_krw": {"width": 22, "number_format": "#,##0"},
                "counterparty": {"width": 28},
                "memo": {"width": 70, "wrap": True},
                "requirement": {"width": 14},
                "why": {"width": 22, "wrap": True},
                "next_action": {"width": 70, "wrap": True},
            },
            table_name_prefix="MISS",
        )

        # 증빙
        ws3 = wb.create_sheet("증빙")
        _apply_table(
            ws3,
            ev_header,
            evidence_index_records,
            col_specs={
                "tx_id": {"width": 12},
                "requirement": {"width": 14},
                "status": {"width": 14},
                "note": {"width": 40, "wrap": True},
                "file_key": {"width": 38},
                "original_filename": {"width": 70, "wrap": True},
                "mime_type": {"width": 20},
                "size_bytes": {"width": 16, "number_format": "#,##0"},
                "sha256": {"width": 26},
                "uploaded_at_kst": {"width": 26},
                "attachment_zip_path": {"width": 45},
            },
            table_name_prefix="EV",
        )

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _xlsx_bytes_make_checklist() -> bytes:
        """서류체크리스트.xlsx 생성"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment
        except Exception:
            return b""

        from services.tax_package_excel_style import style_sheet, StyleConfig

        header = ["group", "required", "item", "where", "status", "note"]
        wb = Workbook()
        ws = wb.active
        ws.title = "체크리스트"

        ws.append(header)
        for r in checklist_rows:
            ws.append([r.get(h, "") for h in header])

        # ✅ 표 스타일(한글 헤더/격자/외곽 두꺼움/폭/헤더높이 포함)
        style_sheet(ws, table_prefix="CHK", cfg=StyleConfig(apply_cell_borders=True, max_col_width=80.0))

        # 가이드 시트
        ws2 = wb.create_sheet("가이드")
        ws2["A1"] = "서류체크리스트 사용법"
        ws2["A1"].font = Font(bold=True, size=13)
        ws2["A3"] = "• status는 '미준비/준비중/준비완료/해당없음/자동 포함/일부 포함' 중 선택해서 정리하세요."
        ws2["A4"] = "• 거래내역/증빙(앱 자동 포함) 외 서류는 '04_홈택스_및_연간필수자료' / '05_추가서류' 폴더에 넣어 전달하세요."
        ws2.column_dimensions["A"].width = 110
        ws2.sheet_view.showGridLines = False
        ws2.sheet_view.zoomScale = 110
        ws2["A3"].alignment = Alignment(vertical="top", wrap_text=True)
        ws2["A4"].alignment = Alignment(vertical="top", wrap_text=True)
        ws2.row_dimensions[3].height = 36
        ws2.row_dimensions[4].height = 36

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _xlsx_bytes_make_quality_report() -> bytes:
        """품질리포트.xlsx 생성 (사전 통과 점검 결과)."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except Exception:
            return b""

        from services.tax_package_excel_style import style_sheet, StyleConfig

        wb = Workbook()
        ws0 = wb.active
        ws0.title = "판정요약"
        ws0["A1"] = "세무사 전달 사전 점검(Preflight)"
        ws0["A1"].font = Font(bold=True, size=14)
        ws0["A3"] = "판정"
        ws0["B3"] = preflight["status"]
        ws0["A4"] = "결론"
        ws0["B4"] = preflight["verdict"]
        ws0["A5"] = "Fail"
        ws0["B5"] = preflight["fail_count"]
        ws0["A6"] = "Warn"
        ws0["B6"] = preflight["warn_count"]
        ws0["A7"] = "필수 증빙 첨부율"
        ws0["B7"] = preflight["required_attachment_rate_pct"]
        ws0["A8"] = "중복 의심"
        ws0["B8"] = preflight["duplicate_suspects_count"]
        ws0.column_dimensions["A"].width = 28
        ws0.column_dimensions["B"].width = 54
        ws0.sheet_view.showGridLines = False

        ws1 = wb.create_sheet("점검결과")
        header = ["code", "title", "level", "status", "metric", "target", "message"]
        ws1.append(header)
        for c in preflight["checks"]:
            ws1.append([c.get(h, "") for h in header])
        style_sheet(ws1, table_prefix="PCHK", cfg=StyleConfig(apply_cell_borders=True, max_col_width=80.0))

        ws2 = wb.create_sheet("중복의심")
        header2 = ["direction", "amount_krw", "counterparty", "tx_id_a", "date_a", "tx_id_b", "date_b", "distance_days", "why"]
        ws2.append(header2)
        for r in duplicate_suspects[:300]:
            ws2.append([r.get(h, "") for h in header2])
        style_sheet(ws2, table_prefix="DUP", cfg=StyleConfig(apply_cell_borders=True, max_col_width=80.0))

        ws3 = wb.create_sheet("누락요약")
        header3 = ["priority", "tx_id", "date_kst", "amount_krw", "counterparty", "why", "next_action"]
        ws3.append(header3)
        for r in missing_list[:300]:
            ws3.append([r.get(h, "") for h in header3])
        style_sheet(ws3, table_prefix="MISSQ", cfg=StyleConfig(apply_cell_borders=True, max_col_width=80.0))

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    DIR_SUMMARY = "00_세무사_요약"
    DIR_SHEETS = "01_정리표"
    DIR_RAW = "02_원장_원본데이터(raw)"
    DIR_ATTACH = "03_증빙첨부(attachments)"
    DIR_HOMETAX = "04_홈택스_및_연간필수자료(사용자추가)"
    DIR_EXTRA = "05_추가서류(사용자추가)"
    profile_summary = tax_profile_summary(user_pk)

    # 1) README / manifest / 품질리포트
    _wtext(f"{DIR_SUMMARY}/README_세무사용.txt", _render_accountant_readme(stats))
    _wtext(f"{DIR_SUMMARY}/profile_summary.txt", _render_profile_summary(profile_summary))
    _wtext(f"{DIR_SUMMARY}/검증리포트.txt", _render_validation_report_text(validation_report))

    manifest = {
        "schema": "SafeToSpend_TaxPackage",
        "schema_version": 3,
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
            "attachments_count": int(attachments_count),
            "duplicate_suspects_count": int(preflight["duplicate_suspects_count"]),
        },
        "tax_buffer": {
            "default_tax_rate": stats.tax_rate,
            "tax_buffer_total": stats.tax_buffer_total,
            "tax_buffer_target": stats.tax_buffer_target,
            "tax_buffer_shortage": stats.tax_buffer_shortage,
            "tax_due_est_krw": int(tax_metrics.get("tax_due_est_krw") or 0),
            "tax_calculation_mode": str(tax_metrics.get("tax_calculation_mode") or "unknown"),
            "official_calculable": bool(tax_metrics.get("official_calculable")),
            "is_limited_estimate": bool(tax_metrics.get("is_limited_estimate")),
            "official_block_reason": str(tax_metrics.get("official_block_reason") or ""),
            "taxable_income_input_source": str(tax_metrics.get("taxable_income_input_source") or "missing"),
        },
        "preflight": {
            **preflight,
            "duplicate_suspects_preview": duplicate_suspects[:50],
        },
        "validation_report": validation_report,
        "files": {
            "summary_dir": f"{DIR_SUMMARY}/",
            "profile_summary_file": f"{DIR_SUMMARY}/profile_summary.txt",
            "sheets_dir": f"{DIR_SHEETS}/",
            "raw_dir": f"{DIR_RAW}/",
            "attachments_dir": f"{DIR_ATTACH}/attachments/",
            "hometax_docs_dir": f"{DIR_HOMETAX}/",
            "extra_docs_dir": f"{DIR_EXTRA}/",
        },
        "notes": {
            "generated_by": "SafeToSpend(쓸수있어)",
            "disclaimer": "본 자료는 참고용이며 최종 신고 판단은 세무사/국세청 기준에 따릅니다.",
        },
    }
    _wjson(f"{DIR_SUMMARY}/manifest.json", manifest)
    xlsxq = _xlsx_bytes_make_quality_report()
    if xlsxq:
        z.writestr(f"{root}/{DIR_SUMMARY}/품질리포트.xlsx", xlsxq)

    # 2) Excel (메인)
    xlsx1 = _xlsx_bytes_make_summary_and_tables()
    if xlsx1:
        z.writestr(f"{root}/{DIR_SHEETS}/세무사용_정리표.xlsx", xlsx1)

    xlsx2 = _xlsx_bytes_make_checklist()
    if xlsx2:
        z.writestr(f"{root}/{DIR_SHEETS}/서류체크리스트.xlsx", xlsx2)

    # 3) raw XLSX (검증/원본)
    _wxlsx(f"{DIR_RAW}/transactions.xlsx", "transactions", tx_header, tx_records, table_prefix="RAW_TX", apply_borders=False)
    _wxlsx(f"{DIR_RAW}/evidence_index.xlsx", "evidence_index", ev_header, evidence_index_records, table_prefix="RAW_EV", apply_borders=False)
    _wxlsx(f"{DIR_RAW}/missing_evidence.xlsx", "missing_evidence", miss_header, missing_list[:200], table_prefix="RAW_MISS", apply_borders=True)

    # 4) attachments
    _wtext(
        f"{DIR_ATTACH}/README_증빙파일규칙.txt",
        "증빙 파일은 attachments_index.xlsx의 attachment_zip_path 컬럼 기준으로 찾을 수 있습니다.\n"
        "파일명 규칙: YYYYMMDD_HHMMSS_금액원_거래처_증빙종류_순번.ext\n"
        "(fallback) 시각 없음=시간미상, 금액 없음=금액미상, 거래처 없음=거래처미상, 증빙종류 불명=증빙\n",
    )
    _wxlsx(
        f"{DIR_ATTACH}/attachments_index.xlsx",
        "attachments_index",
        attachments_index_header,
        attachments_index_records,
        table_prefix="ATTIDX",
        apply_borders=True,
    )
    for a in attachments:
        try:
            zip_path = a["zip_path"]
            abs_path = a["abs_path"]
            with abs_path.open("rb") as f:
                z.writestr(f"{root}/{zip_path}", f.read())
        except Exception:
            continue

    # 5) 사용자 추가 폴더 가이드
    _wtext(f"{DIR_HOMETAX}/README.txt", "홈택스/연간 필수 자료를 아래 세부 폴더에 넣어주세요.\n")
    _wtext(f"{DIR_HOMETAX}/01_지급명세서_원천징수/README.txt", "지급명세서, 원천징수영수증을 넣어주세요.\n")
    _wtext(f"{DIR_HOMETAX}/02_카드사용내역_현금영수증(지출증빙)/README.txt", "카드내역/현금영수증(앱 외 보완자료)을 넣어주세요.\n")
    _wtext(f"{DIR_HOMETAX}/03_전자세금계산서_계산서_매출매입/README.txt", "전자세금계산서/계산서(매출·매입)를 넣어주세요.\n")
    _wtext(f"{DIR_HOMETAX}/04_부가세신고자료(해당시)/README.txt", "부가세 신고/확인 관련 자료를 넣어주세요.\n")

    _wtext(f"{DIR_EXTRA}/README.txt", "기본서류/공제자료/기타 참고 서류를 아래 폴더에 넣어주세요.\n")
    _wtext(
        f"{DIR_EXTRA}/01_기본서류(신분_계좌_사업자)/README.txt",
        "신분증/주민등록/가족관계 서류는 기본 업로드 대상이 아니에요.\n필요할 때만 세무사와 합의한 별도 안전 채널로 전달해 주세요.\n",
    )
    _wtext(f"{DIR_EXTRA}/02_공제자료(연금_보험_기부_노란우산_등)/README.txt", "연금/보험/기부/노란우산 등 공제자료를 넣어주세요.\n")
    _wtext(f"{DIR_EXTRA}/03_임대차_대출이자_차량_통신비_등(해당시)/README.txt", "임대차/대출이자/차량/통신비 등 추가 근거를 넣어주세요.\n")

    z.close()
    out.seek(0)

    filename = f"SafeToSpend_TaxPackage_{month_key}.zip"
    return out, filename


# -----------------------------
# Helpers
# -----------------------------
def _build_missing_list(tx_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """월 거래 중 '사용자/세무사 액션이 필요한 것'만 우선순위 리스트로."""
    candidates: list[dict[str, Any]] = []

    for r in tx_records:
        direction = r.get("direction")
        amt = _safe_int(r.get("amount_krw"))
        cp = r.get("counterparty") or ""
        memo = r.get("memo") or ""
        date_kst = r.get("date_kst") or ""
        tx_id = r.get("tx_id")

        # P0: 필수 증빙 누락
        if direction == "out" and r.get("evidence_status") == "missing" and r.get("evidence_requirement") == "required":
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
        if direction == "out" and r.get("evidence_status") == "missing" and r.get("evidence_requirement") == "maybe":
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
    est_profit = stats.income_included_total - stats.expense_business_total

    lines: list[str] = []
    lines.append("[쓸수있어(SafeToSpend) 세무사 전달 패키지]")
    lines.append(f"- 대상 월: {stats.month_key}")
    lines.append(f"- 기간(KST): {stats.period_start_kst} ~ {stats.period_end_kst}")
    lines.append(f"- 생성일(KST): {stats.generated_at_kst}")
    lines.append("")

    lines.append("1) 포함 파일(권장 순서)")
    lines.append("- 00_세무사_요약/README_세무사용.txt : 세무사 1분 판단용 안내")
    lines.append("- 00_세무사_요약/품질리포트.xlsx : 통과/보완 자동 점검 결과")
    lines.append("- 01_정리표/세무사용_정리표.xlsx : (추천) 요약/거래/누락/증빙 정리표")
    lines.append("- 03_증빙첨부(attachments)/attachments_index.xlsx : 거래↔첨부 매핑표")
    lines.append("- 03_증빙첨부(attachments)/attachments/ : 사용자가 업로드한 증빙 파일")
    lines.append("- 02_원장_원본데이터(raw)/ : 검증용 원장 데이터")
    lines.append("- 04_홈택스_및_연간필수자료(사용자추가)/, 05_추가서류(사용자추가)/ : 추가 자료 슬롯")
    lines.append("- 거래 시트의 bank_account 컬럼은 '별칭 + ****1234' 또는 '미지정'으로 표시됩니다.")
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
    lines.append(f"- 경비 혼재/미확정 합계: {_krw(stats.expense_mixed_total + stats.expense_unknown_total)}")
    lines.append("")

    lines.append("4) 사용 방법(권장)")
    lines.append("- (A) 00_세무사_요약/품질리포트.xlsx로 통과/보완 상태를 먼저 확인")
    lines.append("- (B) 01_정리표/세무사용_정리표.xlsx → '누락' 시트에서 P0(필수)부터 확인")
    lines.append("- (C) attachments_index.xlsx의 attachment_zip_path로 첨부파일을 즉시 매칭")
    lines.append("- (D) 04/05 폴더 README 가이드에 따라 추가 자료를 보완")
    lines.append("")

    lines.append("[주의]")
    lines.append("- 본 자료는 사용자가 제공/연동한 거래 및 업로드된 증빙을 정리한 참고 자료입니다.")
    lines.append("- 신분증/주민등록/가족관계 관련 민감 서류는 기본 패키지에 포함하지 않습니다.")
    lines.append("- 최종 신고 판단 및 세법 적용은 세무사/국세청 기준에 따릅니다.")

    return "\n".join(lines) + "\n"


def _render_validation_report_text(report: dict[str, Any] | None) -> str:
    data = report or {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    missing_top = data.get("missing_top") if isinstance(data.get("missing_top"), list) else []
    duplicate_top = data.get("duplicate_top") if isinstance(data.get("duplicate_top"), list) else []
    outlier_top = data.get("outlier_top") if isinstance(data.get("outlier_top"), list) else []

    completion_rate = summary.get("completion_rate_pct")
    if completion_rate is None:
        completion_line = "증빙 완성률: 해당 없음(증빙 필요 거래 없음)"
    else:
        completion_line = f"증빙 완성률: {float(completion_rate):.1f}%"

    lines: list[str] = []
    lines.append("[검증 리포트]")
    lines.append("이 문서는 세무사 전달 전 점검용 요약입니다. 확정이 아닌 참고/의심 항목을 포함합니다.")
    lines.append("")
    lines.append(f"- 총 거래 수: {_safe_int(summary.get('transaction_total')):,}건")
    lines.append(f"- 증빙 필요 거래 수: {_safe_int(summary.get('evidence_required_total')):,}건")
    lines.append(f"- 증빙 첨부 완료 수: {_safe_int(summary.get('evidence_attached_total')):,}건")
    lines.append(f"- 남은 거래 수: {_safe_int(summary.get('remaining_total')):,}건")
    lines.append(f"- {completion_line}")
    lines.append("")

    lines.append("[A] 누락 가능성 Top 5 (증빙 필요인데 미첨부)")
    if missing_top:
        for idx, row in enumerate(missing_top[:5], start=1):
            lines.append(
                f"{idx}. {row.get('date_kst', '-')}"
                f" | {row.get('counterparty', '(거래처 미기재)')}"
                f" | {_safe_int(row.get('amount_krw')):,}원"
                f" | {row.get('status_label', '확인 필요')}"
            )
    else:
        lines.append("- 누락 의심 항목이 없어요.")
    lines.append("")

    lines.append("[B] 중복 의심 Top 5 (의심)")
    if duplicate_top:
        for idx, row in enumerate(duplicate_top[:5], start=1):
            lines.append(
                f"{idx}. {row.get('counterparty', '(거래처 미기재)')}"
                f" | {_safe_int(row.get('amount_krw')):,}원"
                f" | {row.get('date_a', '-')} / {row.get('date_b', '-')}"
                f" | 간격 {_safe_int(row.get('distance_days'))}일"
            )
    else:
        lines.append("- 중복 의심 항목이 없어요.")
    lines.append("")

    lines.append("[C] 이상치 Top 5 (참고)")
    if outlier_top:
        for idx, row in enumerate(outlier_top[:5], start=1):
            direction = "지출" if str(row.get("direction") or "") == "out" else "수입"
            lines.append(
                f"{idx}. {row.get('date_kst', '-')}"
                f" | {direction}"
                f" | {row.get('counterparty', '(거래처 미기재)')}"
                f" | {_safe_int(row.get('amount_krw')):,}원"
                f" | 평균 대비 {float(row.get('ratio_vs_avg') or 0):.1f}배"
            )
    else:
        lines.append("- 이상치 참고 항목이 없어요.")
    lines.append("")
    lines.append("[주의] 위 항목은 자동 점검 결과입니다. 최종 확정은 사용자/세무사 확인이 필요합니다.")
    return "\n".join(lines) + "\n"


def _render_profile_summary(profile: dict[str, Any]) -> str:
    p = profile or {}
    lines: list[str] = []
    lines.append("[내 정보 요약]")
    lines.append("입력한 정보 기반으로 정리된 요약입니다.")
    lines.append("")
    lines.append(f"- 필수 정보 입력 상태: {'완료' if p.get('is_complete') else '미완료'} ({int(p.get('completion_percent') or 0)}%)")
    lines.append(f"- 업종: {p.get('industry_label', '모름')}")
    lines.append(f"- 과세유형: {p.get('tax_type_label', '모름')}")
    lines.append(f"- 전년도 수입 규모: {p.get('prev_income_band_label', '모름')}")
    lines.append(f"- 원천징수(3.3%): {p.get('withholding_3_3_label', '모름')}")
    lines.append("")
    lines.append(f"- 개업일: {p.get('opening_date_label', '모름')}")
    lines.append(f"- 다른 소득 여부: {p.get('other_income_label', '모름')}")

    other_types = p.get("other_income_types_labels") or []
    if other_types:
        lines.append(f"- 다른 소득 유형: {', '.join([str(x) for x in other_types])}")
    else:
        lines.append("- 다른 소득 유형: -")

    lines.append(f"- 고가장비 취득 여부: {p.get('high_cost_asset_label', '모름')}")
    lines.append(f"- 인건비/외주 지급 여부: {p.get('labor_outsource_label', '모름')}")
    health_line = f"- 건강보험 가입유형: {p.get('health_insurance_type_label', '모름')}"
    monthly = p.get("health_insurance_monthly_krw")
    if monthly is not None:
        try:
            health_line += f" / 월 납부액: {int(monthly):,}원"
        except Exception:
            health_line += " / 월 납부액: -"
    else:
        health_line += " / 월 납부액: -"
    lines.append(health_line)
    lines.append("")
    lines.append("[안내]")
    lines.append("- 본 요약은 사용자가 입력한 값 기준입니다.")
    lines.append("- 최종 신고 판단은 세무사/국세청 기준에 따릅니다.")
    return "\n".join(lines) + "\n"
