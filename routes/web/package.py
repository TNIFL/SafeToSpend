from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, render_template, request, send_file, session
from sqlalchemy import func

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, Transaction
from routes.web.vault import _ensure_month_evidence_rows, _month_dt_range, _month_key, _parse_month
from services.tax_package import build_tax_package_zip


web_package_bp = Blueprint("web_package", __name__, url_prefix="/dashboard")


@web_package_bp.get("/package")
@login_required
def page():
    user_pk = int(session["user_id"])

    month_first = _parse_month(request.args.get("month"))
    month_key = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    _ensure_month_evidence_rows(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    base = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    )

    missing_rows = (
        base.filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement.in_(["required", "maybe"]))
        .all()
    )

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

    attached_total = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.status == "attached")
        .scalar()
    ) or 0

    denom = attached_total + missing_total
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
        missing_rows=missing_rows[:80],
    )


@web_package_bp.get("/package/download")
@login_required
def download():
    user_pk = int(session["user_id"])
    month_key = _month_key(_parse_month(request.args.get("month")))
    zip_io, filename = build_tax_package_zip(user_pk=user_pk, month_key=month_key)
    try:
        zip_io.seek(0)
    except Exception:
        pass
    return send_file(zip_io, as_attachment=True, download_name=filename, mimetype="application/zip", max_age=0)
