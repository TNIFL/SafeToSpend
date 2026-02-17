# routes/web/vault_page.py
from __future__ import annotations

from datetime import date, datetime, time

from flask import render_template, request, session, redirect, url_for, flash
from sqlalchemy import and_

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, Transaction


def _parse_month_key(s: str | None) -> str:
    if not s:
        return date.today().strftime("%Y-%m")
    s = (s or "").strip()
    # "YYYY-MM" 최소 검증
    if len(s) != 7 or s[4] != "-":
        raise ValueError("월 형식이 올바르지 않습니다. (YYYY-MM)")
    y = int(s[:4])
    m = int(s[5:7])
    if m < 1 or m > 12:
        raise ValueError("월 형식이 올바르지 않습니다. (YYYY-MM)")
    return f"{y:04d}-{m:02d}"


def _month_range_naive_from_key(month_key: str) -> tuple[datetime, datetime]:
    y = int(month_key[:4])
    m = int(month_key[5:7])
    start = datetime(y, m, 1, 0, 0, 0)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0)
    return start, end


def _date_str(dt: datetime) -> str:
    # occurred_at이 naive(KST 가정)인 프로젝트 기준으로 단순 포맷
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


@login_required
def vault_page():
    """
    /dashboard/vault 페이지 렌더링.
    - 기존 web_calendar.py 안에 vault()가 없거나 등록이 안 되었을 때를 대비한 '안전한' 페이지 라우트
    """
    user_pk = int(session["user_id"])

    try:
        month_key = _parse_month_key(request.args.get("month"))
    except Exception as e:
        flash(str(e) or "월 형식이 올바르지 않습니다. (YYYY-MM)", "error")
        return redirect(url_for("web_calendar.month_calendar"))

    start_dt, end_dt = _month_range_naive_from_key(month_key)

    rows = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(and_(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt))
        .order_by(Transaction.occurred_at.desc(), EvidenceItem.id.desc())
        .all()
    )

    items = []
    attached = 0
    missing = 0

    for ev, tx in rows:
        has_file = bool(ev.file_key)
        if has_file:
            attached += 1
        elif ev.requirement in ("required", "maybe") and ev.status == "missing":
            missing += 1

        items.append(
            dict(
                evidence_id=ev.id,
                date=_date_str(tx.occurred_at),
                counterparty=tx.counterparty or "거래처 미상",
                amount=int(tx.amount_krw or 0),
                requirement=ev.requirement,
                status=ev.status,
                has_file=has_file,
                filename=(ev.original_filename or ""),
                retention_until=(ev.retention_until.isoformat() if ev.retention_until else ""),
            )
        )

    counts = {"attached": attached, "missing": missing, "total": len(items)}
    return render_template("calendar/vault.html", month_key=month_key, items=items, counts=counts)
