# routes/web/vault.py
from __future__ import annotations

import io, zipfile
import re

from datetime import date, datetime, time, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from sqlalchemy import func, or_, case

from pathlib import Path

from core.auth import login_required
from core.extensions import db
from core.security import safe_referrer_or_fallback
from core.time import kstnow, utcnow
from domain.models import EvidenceItem, ExpenseLabel, Transaction
from services.evidence_vault import (
    default_retention_until,
    delete_physical_file,
    resolve_file_path,
    store_evidence_file,
)

web_vault_bp = Blueprint("web_vault", __name__, url_prefix="/dashboard")


# -----------------------------
# Month helpers (web_calendar.py와 호환)
# -----------------------------
def _parse_month(s: str | None) -> date:
    # "YYYY-MM" -> date(YYYY,MM,1)
    if not s:
        today = kstnow().date()
        return date(today.year, today.month, 1)
    raw = str(s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", raw)
    if not m:
        today = kstnow().date()
        return date(today.year, today.month, 1)
    try:
        y = int(m.group(1))
        mm = int(m.group(2))
        if y < 2000 or y > 2100 or mm < 1 or mm > 12:
            raise ValueError("out_of_range")
        return date(y, mm, 1)
    except Exception:
        today = kstnow().date()
        return date(today.year, today.month, 1)


def _month_key(first_day: date) -> str:
    return first_day.strftime("%Y-%m")


def _month_range(first_day: date) -> tuple[date, date]:
    # [start, end)
    if first_day.month == 12:
        end = date(first_day.year + 1, 1, 1)
    else:
        end = date(first_day.year, first_day.month + 1, 1)
    return first_day, end


def _month_dt_range(first_day: date) -> tuple[datetime, datetime]:
    start_d, end_d = _month_range(first_day)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)
    return start_dt, end_dt


def _evidence_defaults_from_expense_status(expense_status: str | None) -> tuple[str, str]:
    """
    ExpenseLabel.status -> EvidenceItem(requirement, status) 기본값
    """
    if expense_status == "business":
        return "required", "missing"
    if expense_status == "personal":
        return "not_needed", "not_needed"
    # unknown/mixed/None
    return "maybe", "missing"


def _ensure_month_evidence_rows(user_pk: int, start_dt: datetime, end_dt: datetime) -> int:
    """
    해당 월의 '지출 거래(Transaction.direction=out)'에 대해 EvidenceItem이 없으면 생성한다.
    (누락도 보관함에서 "항상" 보이게 하기 위해)
    """
    rows = (
        db.session.query(Transaction.id, ExpenseLabel.status, EvidenceItem.id)
        .select_from(Transaction)
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .outerjoin(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )

    created = 0
    for tx_id, expense_status, evidence_id in rows:
        if evidence_id is not None:
            continue
        requirement, st = _evidence_defaults_from_expense_status(expense_status)
        ev = EvidenceItem(
            user_pk=user_pk,
            transaction_id=tx_id,
            requirement=requirement,
            status=st,
            note=None,
        )
        db.session.add(ev)
        created += 1

    if created:
        db.session.commit()

    return created


# -----------------------------
# Views
# -----------------------------
@web_vault_bp.get("/vault")
@login_required
def index():
    user_pk = int(session["user_id"])

    month_first = _parse_month(request.args.get("month"))
    month = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    # 보관함에 "누락까지 포함"해서 보이도록 증빙 row 없으면 생성
    _ensure_month_evidence_rows(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    status = (request.args.get("status") or "all").strip()
    if status not in ("all", "missing", "attached", "not_needed"):
        status = "all"

    req = (request.args.get("req") or "all").strip()
    if req not in ("all", "required", "maybe", "not_needed"):
        req = "all"

    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit") or 200)
    limit = max(20, min(limit, 500))

    base = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )

    if status != "all":
        base = base.filter(EvidenceItem.status == status)
    if req != "all":
        base = base.filter(EvidenceItem.requirement == req)
    if q:
        like = f"%{q}%"
        base = base.filter(
            or_(
                Transaction.counterparty.ilike(like),
                Transaction.memo.ilike(like),
                EvidenceItem.original_filename.ilike(like),
            )
        )

    rows = (
        base.order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
        .all()
    )

    # 상단 요약/배지 (✅ func.case -> case 로 수정)
    agg = (
        db.session.query(
            func.sum(case((EvidenceItem.status == "missing", 1), else_=0)),
            func.sum(case((EvidenceItem.status == "attached", 1), else_=0)),
            func.sum(case((EvidenceItem.status == "not_needed", 1), else_=0)),
            func.sum(
                case(
                    (((EvidenceItem.status == "missing") & (EvidenceItem.requirement == "required")), 1),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (((EvidenceItem.status == "missing") & (EvidenceItem.requirement == "maybe")), 1),
                    else_=0,
                )
            ),
            func.count(EvidenceItem.id),
        )
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .one()
    )

    missing_all = int(agg[0] or 0)
    attached_all = int(agg[1] or 0)
    not_needed_all = int(agg[2] or 0)
    missing_required = int(agg[3] or 0)
    missing_maybe = int(agg[4] or 0)
    total = int(agg[5] or 0)

    # month nav
    prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1)

    return render_template(
        "vault/index.html",
        month_key=month,
        month_first=month_first,
        prev_month=prev_month.strftime("%Y-%m"),
        next_month=next_month.strftime("%Y-%m"),
        status=status,
        req=req,
        q=q,
        limit=limit,
        missing_all=missing_all,
        attached_all=attached_all,
        not_needed_all=not_needed_all,
        missing_required=missing_required,
        missing_maybe=missing_maybe,
        total=total,
        rows=rows,
    )


@web_vault_bp.post("/vault/upload/<int:tx_id>")
@login_required
def upload(tx_id: int):
    user_pk = int(session["user_id"])

    # 파일
    f = request.files.get("file")
    if not f or not f.filename:
        flash("파일을 선택해 주세요.", "error")
        return redirect(safe_referrer_or_fallback(req=request, fallback=url_for("web_vault.index")))

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev:
        # 해당 거래가 없다면 404
        abort(404)

    try:
        # month_key는 거래일 기준으로 맞춤(occurred_at이 naive이면 그대로 사용)
        month_first = _parse_month(request.args.get("month"))
        month_key = _month_key(month_first)

        stored = store_evidence_file(
            user_pk=user_pk,
            tx_id=tx_id,
            month_key=month_key,
            file=f,
        )

        ev.file_key = stored.file_key
        ev.original_filename = stored.original_filename
        ev.mime_type = stored.mime_type
        ev.size_bytes = int(stored.size_bytes)
        ev.sha256 = stored.sha256
        ev.uploaded_at = utcnow()
        ev.deleted_at = None
        ev.retention_until = default_retention_until()
        ev.status = "attached"
        db.session.commit()

        flash("증빙이 업로드되었습니다.", "success")
    except Exception:
        db.session.rollback()
        flash("업로드 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")

    return redirect(safe_referrer_or_fallback(req=request, fallback=url_for("web_vault.index")))


@web_vault_bp.post("/vault/delete/<int:tx_id>")
@login_required
def delete(tx_id: int):
    user_pk = int(session["user_id"])

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev:
        abort(404)

    # 물리 파일 삭제(가능하면)
    try:
        delete_physical_file(ev.file_key)
    except Exception:
        pass

    # 메타데이터만 정리
    ev.file_key = None
    ev.original_filename = None
    ev.mime_type = None
    ev.size_bytes = None
    ev.sha256 = None
    ev.uploaded_at = None
    ev.deleted_at = utcnow()
    ev.status = "missing" if ev.requirement in ("required", "maybe") else "not_needed"

    db.session.commit()
    flash("삭제되었습니다.", "success")
    return redirect(safe_referrer_or_fallback(req=request, fallback=url_for("web_vault.index")))


@web_vault_bp.get("/vault/file/<int:tx_id>")
@login_required
def download(tx_id: int):
    user_pk = int(session["user_id"])

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev or not ev.file_key:
        abort(404)

    try:
        path = resolve_file_path(ev.file_key)
    except Exception:
        abort(404)
    if not path.exists():
        abort(404)

    return send_file(path, as_attachment=True, download_name=ev.original_filename or path.name)


@web_vault_bp.post("/vault/set")
@login_required
def set_status():
    user_pk = int(session["user_id"])

    tx_id = int(request.form.get("tx_id") or 0)
    if not tx_id:
        abort(400)

    requirement = (request.form.get("requirement") or "").strip()
    status = (request.form.get("status") or "").strip()
    note = (request.form.get("note") or "").strip()

    if requirement not in ("required", "maybe", "not_needed"):
        requirement = "maybe"
    if status not in ("missing", "attached", "not_needed"):
        status = "missing"

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev:
        abort(404)

    ev.requirement = requirement
    ev.status = status
    ev.note = note or None

    # not_needed면 파일 메타는 유지하되, 화면상 "불필요"로 표시됨
    db.session.commit()

    flash("저장했습니다.", "success")
    return redirect(safe_referrer_or_fallback(req=request, fallback=url_for("web_vault.index")))


@web_vault_bp.get("/vault/export-all")
@login_required
def export_all():
    user_pk = int(session["user_id"])

    # 첨부된 것만 백업(삭제된 것/없는 파일은 스킵)
    rows = (
        db.session.query(EvidenceItem)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(EvidenceItem.file_key.isnot(None))
        .filter(EvidenceItem.status == "attached")
        .order_by(EvidenceItem.id.desc())
        .all()
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        for ev in rows:
            try:
                p = resolve_file_path(ev.file_key)
                if not p.exists() or not p.is_file():
                    continue

                base = secure_filename(ev.original_filename or p.name) or p.name
                tx_id = ev.transaction_id
                zip_name = f"{tx_id}_{base}"

                with p.open("rb") as f:
                    z.writestr(zip_name, f.read())
            except Exception:
                continue

        # 인덱스(검증용)
        lines = ["transaction_id,original_filename,uploaded_at,status\n"]
        for ev in rows:
            lines.append(
                f"{ev.transaction_id},{(ev.original_filename or '')},{(ev.uploaded_at or '')},{(ev.status or '')}\n"
            )
        z.writestr("index.csv", "".join(lines).encode("utf-8-sig"))

    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"SafeToSpend_Evidence_Backup_{ts}.zip",
    )


@web_vault_bp.post("/vault/delete-all")
@login_required
def delete_all():
    user_pk = int(session["user_id"])

    # "첨부된 증빙 전체 삭제" (DB + 디스크)
    rows = (
        db.session.query(EvidenceItem)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(EvidenceItem.file_key.isnot(None))
        .all()
    )

    deleted_files = 0
    for ev in rows:
        if ev.file_key:
            try:
                delete_physical_file(ev.file_key)
                deleted_files += 1
            except Exception:
                pass
        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = utcnow()
        # 상태는 "missing"으로 되돌리는 게 vault UX상 자연스러움
        if ev.status == "attached":
            ev.status = "missing"

    db.session.commit()
    flash(f"전체 삭제 완료: {deleted_files}개 파일", "ok")
    return redirect(url_for("web_vault.index", **request.args))


@web_vault_bp.post("/vault/purge-month-files")
@login_required
def purge_month_files():
    user_pk = int(session["user_id"])
    month_first = _parse_month(request.form.get("month"))
    month_key = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    rows = (
        db.session.query(EvidenceItem)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.file_key.isnot(None))
        .all()
    )

    deleted_files = 0
    for ev in rows:
        if ev.file_key:
            try:
                delete_physical_file(ev.file_key)
                deleted_files += 1
            except Exception:
                pass
        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = utcnow()
        if ev.status == "attached":
            ev.status = "missing" if ev.requirement in ("required", "maybe") else "not_needed"

    db.session.commit()
    flash(f"{month_key} 파일 전체 삭제 완료: {deleted_files}개", "ok")
    return redirect(url_for("web_vault.index", month=month_key))
