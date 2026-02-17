# routes/web/vault.py
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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

from sqlalchemy import func, or_, case

from core.auth import login_required
from core.extensions import db
from domain.models import EvidenceItem, ExpenseLabel, Transaction
from services.evidence_vault import (
    default_retention_until,
    delete_physical_file,
    resolve_file_path,
    store_evidence_file,
)

KST = ZoneInfo("Asia/Seoul")

web_vault_bp = Blueprint("web_vault", __name__, url_prefix="/dashboard")


# -----------------------------
# Month helpers (web_calendar.py와 호환)
# -----------------------------
def _parse_month(s: str | None) -> date:
    # "YYYY-MM" -> date(YYYY,MM,1)
    if not s:
        today = datetime.now(timezone.utc).astimezone(KST).date()
        return date(today.year, today.month, 1)
    y, m = s.split("-")
    return date(int(y), int(m), 1)


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

    # 상단 요약/배지
    # ✅ SQLAlchemy 2.x: func.case(...)가 아니라 sqlalchemy.case(...)를 사용해야 함
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

    tx = Transaction.query.filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        flash("거래를 찾을 수 없습니다.", "error")
        return redirect(url_for("web_vault.index"))

    file = request.files.get("file")
    if not file or not file.filename:
        flash("업로드할 파일을 선택해 주세요.", "error")
        return redirect(request.referrer or url_for("web_vault.index"))

    # EvidenceItem row 확보
    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev:
        exp = ExpenseLabel.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        requirement, st = _evidence_defaults_from_expense_status(exp.status if exp else None)
        ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement=requirement, status=st, note=None)
        db.session.add(ev)
        db.session.commit()

    # 기존 파일이 있으면 먼저 삭제(“교체 업로드”)
    if ev.file_key:
        delete_physical_file(ev.file_key)

    # month_key 추정
    try:
        mk = tx.occurred_at.strftime("%Y-%m")
    except Exception:
        mk = datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m")

    try:
        stored = store_evidence_file(
            user_pk=user_pk,
            tx_id=tx_id,
            month_key=mk,
            file=file,
        )
    except ValueError as e:
        flash(str(e), "error")
        return redirect(request.referrer or url_for("web_vault.index", month=mk))
    except Exception:
        flash("파일 저장 중 문제가 발생했습니다.", "error")
        return redirect(request.referrer or url_for("web_vault.index", month=mk))

    # DB 메타데이터 업데이트
    ev.file_key = stored.file_key
    ev.original_filename = stored.original_filename
    ev.mime_type = stored.mime_type
    ev.size_bytes = stored.size_bytes
    ev.sha256 = stored.sha256
    ev.uploaded_at = datetime.now(timezone.utc)
    ev.deleted_at = None
    ev.retention_until = default_retention_until()

    ev.status = "attached"
    db.session.commit()

    flash("증빙이 업로드되었습니다.", "success")

    next_url = (request.form.get("next") or "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(request.referrer or url_for("web_vault.index", month=mk))


@web_vault_bp.get("/vault/download/<int:tx_id>")
@login_required
def download(tx_id: int):
    user_pk = int(session["user_id"])

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev or not ev.file_key:
        abort(404)

    path = resolve_file_path(ev.file_key)
    if not path.exists():
        abort(404)

    filename = ev.original_filename or path.name
    return send_file(path, as_attachment=True, download_name=filename)


@web_vault_bp.post("/vault/delete/<int:tx_id>")
@login_required
def delete(tx_id: int):
    user_pk = int(session["user_id"])

    ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not ev:
        flash("대상을 찾을 수 없습니다.", "error")
        return redirect(request.referrer or url_for("web_vault.index"))

    if ev.file_key:
        delete_physical_file(ev.file_key)

    ev.file_key = None
    ev.original_filename = None
    ev.mime_type = None
    ev.size_bytes = None
    ev.sha256 = None
    ev.uploaded_at = None
    ev.deleted_at = datetime.now(timezone.utc)
    ev.retention_until = None

    # requirement에 따라 상태 복구
    if ev.requirement == "not_needed":
        ev.status = "not_needed"
    else:
        ev.status = "missing"

    db.session.commit()

    flash("삭제되었습니다.", "success")
    return redirect(request.referrer or url_for("web_vault.index"))


@web_vault_bp.post("/vault/purge")
@login_required
def purge_month_files():
    """
    선택한 월의 '첨부된 파일'을 전부 즉시 삭제(서버 저장본 제거).
    거래/증빙 row는 남기고, status는 requirement에 따라 missing/not_needed로 복구.
    """
    user_pk = int(session["user_id"])
    month_first = _parse_month(request.form.get("month"))
    month = _month_key(month_first)
    start_dt, end_dt = _month_dt_range(month_first)

    q = (
        db.session.query(EvidenceItem, Transaction)
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.file_key.isnot(None))
        .filter(EvidenceItem.status == "attached")
        .all()
    )

    deleted = 0
    for ev, _tx in q:
        if ev.file_key:
            delete_physical_file(ev.file_key)
        ev.file_key = None
        ev.original_filename = None
        ev.mime_type = None
        ev.size_bytes = None
        ev.sha256 = None
        ev.uploaded_at = None
        ev.deleted_at = datetime.now(timezone.utc)
        ev.retention_until = None
        if ev.requirement == "not_needed":
            ev.status = "not_needed"
        else:
            ev.status = "missing"
        deleted += 1

    db.session.commit()

    flash(f"이번 달 서버 저장본 {deleted}개를 삭제했습니다.", "success")
    return redirect(url_for("web_vault.index", month=month))
