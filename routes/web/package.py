# routes/web/package.py
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, send_file, session
from sqlalchemy import func

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, ExpenseLabel, Transaction
from services.evidence_vault import resolve_file_path

# vault의 월/생성 로직 재사용
from routes.web.vault import _ensure_month_evidence_rows, _month_dt_range, _month_key, _parse_month

KST = ZoneInfo("Asia/Seoul")

# ✅ 기본은 /dashboard/package
# 만약 네가 진짜로 /package 를 쓰고 싶으면 url_prefix="/dashboard" 를 "" 로 바꾸면 됨.
web_package_bp = Blueprint("web_package", __name__, url_prefix="/dashboard")


def _krw(n: int) -> str:
    return f"{int(n or 0):,}원"


def _safe_date(dt: datetime | None) -> str:
    if not dt:
        return "unknown"
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


@web_package_bp.get("/package")
@login_required
def page():
    # ✅ user_id를 인자로 받지 않는다. (세션에서 꺼낸다)
    user_pk = int(session["user_id"])

    month_first = _parse_month(request.args.get("month"))
    month_key = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    # 증빙 row 보장(누락도 리스트에 포함시키기 위해)
    _ensure_month_evidence_rows(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    # 월 거래(지출 중심 + 증빙 join)
    base = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    )

    # 누락 리스트(필수/검토인데 missing 인 것)
    missing_rows = (
        base.filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement.in_(["required", "maybe"]))
        .all()
    )

    # 상단 지표
    gross_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "in")
        .scalar()
    ) or 0

    total_out = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "out")
        .scalar()
    ) or 0

    missing_total = len(missing_rows)
    missing_required = sum(1 for ev, _ in missing_rows if ev.requirement == "required")
    missing_maybe = sum(1 for ev, _ in missing_rows if ev.requirement == "maybe")

    # 첨부/누락 진행률 계산용 (해당 월의 evidence 상태 카운트)
    attached_total = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.status == "attached")
        .scalar()
    ) or 0

    # 누락은 required/maybe + missing
    denom = attached_total + missing_total

    # month nav (항상 안전한 방식)
    prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
    next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1).strftime("%Y-%m")

    return render_template(
        "package/index.html",
        month_key=month_key,
        month_first=month_first,
        prev_month=prev_month,
        next_month=next_month,
        gross_income=int(gross_income),
        total_out=int(total_out),
        missing_total=int(missing_total),
        missing_required=int(missing_required),
        missing_maybe=int(missing_maybe),
        attached_total=int(attached_total),
        denom=int(denom),
        missing_rows=missing_rows[:80],  # 화면은 최대 80건만
    )


@web_package_bp.get("/package/download")
@login_required
def download():
    user_pk = int(session["user_id"])

    month_first = _parse_month(request.args.get("month"))
    month_key = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    # 월 범위 내 지출 거래 + 증빙
    rows = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        .all()
    )

    # 인덱스 CSV
    ledger_header = [
        "date",
        "direction",
        "amount_krw",
        "counterparty",
        "memo",
        "evidence_requirement",
        "evidence_status",
        "original_filename",
        "note",
    ]

    ledger_records = []
    missing_records = []

    for ev, tx in rows:
        d = tx.occurred_at
        try:
            if d and d.tzinfo is None:
                # naive면 UTC로 가정한 뒤 KST로 표시
                d = d.replace(tzinfo=timezone.utc).astimezone(KST)
        except Exception:
            pass

        date_str = d.strftime("%Y-%m-%d") if d else ""

        ledger_records.append(
            [
                date_str,
                tx.direction,
                int(tx.amount_krw or 0),
                tx.counterparty or "",
                tx.memo or "",
                ev.requirement or "",
                ev.status or "",
                ev.original_filename or "",
                ev.note or "",
            ]
        )

        if tx.direction == "out" and ev.status == "missing" and ev.requirement in ("required", "maybe"):
            why = "필수 증빙 누락" if ev.requirement == "required" else "증빙 확인 필요(미첨부)"
            missing_records.append(
                [
                    "P0" if ev.requirement == "required" else "P2",
                    date_str,
                    int(tx.amount_krw or 0),
                    tx.counterparty or "",
                    why,
                    "카드전표/현금영수증/세금계산서 등 첨부 또는 불필요로 표시",
                ]
            )

    # zip 생성
    mem = io.BytesIO()
    z = zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED)

    def write_csv(path: str, header: list[str], recs: list[list]):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for r in recs:
            w.writerow(r)
        # Excel-friendly UTF-8 BOM
        z.writestr(path, buf.getvalue().encode("utf-8-sig"))

    # README
    z.writestr(
        "README.txt",
        (
            "쓸수있어(SafeToSpend) 세무사 전달 패키지\n"
            f"- 월: {month_key}\n"
            "- 구성:\n"
            "  1) ledger.csv : 거래 + 증빙 상태\n"
            "  2) missing_evidence.csv : 누락(필수/검토) 리스트\n"
            "  3) attachments/ : 첨부된 증빙 파일(가능한 경우)\n\n"
            "[참고]\n"
            "- 'missing_evidence.csv'의 항목은 보관함에서 첨부/불필요 처리하면 줄어듭니다.\n"
        ),
    )

    write_csv("ledger.csv", ledger_header, ledger_records)
    write_csv(
        "missing_evidence.csv",
        ["priority", "date", "amount_krw", "counterparty", "why", "next_action"],
        missing_records,
    )

    # attachments: attached인 것만 실제 파일을 포함(존재하는 경우)
    for ev, tx in rows:
        if ev.status != "attached" or not ev.file_key:
            continue
        p = resolve_file_path(ev.file_key)
        if not p.exists():
            continue

        safe_date = _safe_date(tx.occurred_at)
        orig = ev.original_filename or p.name
        inner = f"attachments/{safe_date}_tx{tx.id}_{orig}"
        if len(inner) > 180:
            inner = f"attachments/{safe_date}_tx{tx.id}_{p.name}"

        try:
            z.write(p, inner)
        except Exception:
            continue

    z.close()
    mem.seek(0)

    filename = f"SafeToSpend_tax_package_{month_key}.zip"
    return send_file(mem, as_attachment=True, download_name=filename, mimetype="application/zip")
