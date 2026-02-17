# routes/web_calendar.py
from __future__ import annotations

from datetime import date, datetime, timedelta, time, timezone
import calendar
import hashlib
from uuid import uuid4

from flask import Blueprint, render_template, request, session, url_for, current_app, redirect, flash, send_file
from sqlalchemy import func, case, cast, Date, or_, and_
from werkzeug.exceptions import Unauthorized
from sqlalchemy.exc import IntegrityError

from core.extensions import db
from domain.models import (
    Transaction, IncomeLabel, ExpenseLabel, EvidenceItem,
    CounterpartyRule, CounterpartyExpenseRule,
    DashboardSnapshot, SafeToSpendSettings, TaxBufferLedger,
    BankAccountLink, RecurringRule
)

from services.risk import compute_risk_summary
from services.tax_package import build_tax_package_zip


web_calendar_bp = Blueprint("web_calendar", __name__, url_prefix="/dashboard")


# -----------------------------
# Utils
# -----------------------------
def utcnow():
    return datetime.now(timezone.utc)


def _parse_month(s: str | None) -> date:
    # "YYYY-MM" -> date(YYYY,MM,1)
    if not s:
        today = date.today()
        return date(today.year, today.month, 1)
    y, m = s.split("-")
    return date(int(y), int(m), 1)


def _month_range(first_day: date) -> tuple[date, date]:
    # [start, end)
    if first_day.month == 12:
        end = date(first_day.year + 1, 1, 1)
    else:
        end = date(first_day.year, first_day.month + 1, 1)
    return first_day, end


def _calendar_grid(first_day: date) -> list[list[date]]:
    # 월요일 시작, 6주(42칸)
    start, _ = _month_range(first_day)
    grid_start = start - timedelta(days=start.weekday())
    days = [grid_start + timedelta(days=i) for i in range(42)]
    return [days[i:i + 7] for i in range(0, 42, 7)]


@web_calendar_bp.before_request
def _require_login():
    # ✅ 프로젝트 방식: session 기반
    if not session.get("user_id"):
        return redirect(url_for("web_auth.login", next=request.full_path))


def _uid() -> int:
    uid = session.get("user_id")
    if not uid:
        raise Unauthorized()
    return int(uid)

# -----------------------------
# Export: 세무사 전달 패키지 (ZIP)
# -----------------------------
@web_calendar_bp.get("/tax-package")
def tax_package():
    """월별 세무사 전달 패키지(zip) 다운로드."""
    user_pk = _uid()
    month_key = (request.args.get("month") or "").strip()

    zip_io, filename = build_tax_package_zip(user_pk=user_pk, month_key=month_key)
    try:
        zip_io.seek(0)
    except Exception:
        pass

    return send_file(
        zip_io,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


def _day_expr_assuming_kst_naive():
    """
    occurred_at이 'KST 기준 naive timestamp'로 저장되어 있다고 가정하면 이게 가장 간단.
    """
    return cast(Transaction.occurred_at, Date)


def _day_expr_assuming_utc_naive_then_kst():
    """
    occurred_at이 'UTC 기준 naive timestamp'로 저장되어 있다면,
    Postgres에서 UTC -> KST로 변환 후 date로 자른다.
    """
    return cast(func.timezone("Asia/Seoul", func.timezone("UTC", Transaction.occurred_at)), Date)


# ✅ 여기만 상황에 맞게 선택
DAY_EXPR = _day_expr_assuming_kst_naive()
# DAY_EXPR = _day_expr_assuming_utc_naive_then_kst()


@web_calendar_bp.app_template_filter("krw")
def krw(n):
    try:
        return f"{int(n or 0):,}원"
    except Exception:
        return "0원"


def _parse_limit(value: str | None, default: int = 200) -> int:
    try:
        limit = int(value or default)
    except Exception:
        limit = default
    return max(20, min(limit, 200))


def _safe_url(endpoint: str, **values):
    try:
        if endpoint in current_app.view_functions:
            return url_for(endpoint, **values)
    except Exception:
        return None
    return None


def _cp_key(name: str | None) -> str | None:
    if not name:
        return None
    k = name.strip()
    if not k:
        return None
    return k


def _month_key_from_tx(tx: Transaction) -> str:
    return tx.occurred_at.strftime("%Y-%m")


def _back_to_review(
    month_key: str,
    focus: str,
    q: str,
    *,
    limit: int | None = None,
    anchor_tx_id: int | None = None,
):
    params = {"month": month_key, "focus": focus, "q": q}
    if limit:
        params["limit"] = int(limit)
    url = url_for("web_calendar.review", **params)
    if anchor_tx_id:
        url = f"{url}#tx-{int(anchor_tx_id)}"
    return redirect(url)


def _next_review_tx_id(
    *,
    user_pk: int,
    focus: str,
    start_dt: datetime,
    end_dt: datetime,
    q: str,
    after_tx: Transaction,
) -> int | None:
    base = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )

    if q:
        like = f"%{q}%"
        base = base.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    if focus == "income_unknown":
        base = (
            base.filter(Transaction.direction == "in")
            .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
            .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
        )
    elif focus == "expense_unknown":
        base = (
            base.filter(Transaction.direction == "out")
            .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
            .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status == "unknown"))
        )
    elif focus == "evidence_required":
        base = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "required")
            .filter(EvidenceItem.status == "missing")
        )
    else:  # evidence_maybe
        base = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "maybe")
            .filter(EvidenceItem.status == "missing")
        )

    # 현재 정렬 기준(occurred_at desc, id desc)에서 "다음" = 더 작은 (occurred_at, id)
    base = base.filter(
        or_(
            Transaction.occurred_at < after_tx.occurred_at,
            and_(Transaction.occurred_at == after_tx.occurred_at, Transaction.id < after_tx.id),
        )
    )

    nxt = base.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).first()
    return int(nxt.id) if nxt else None


def apply_counterparty_rules(user_pk: int, start_dt: datetime, end_dt: datetime) -> None:
    inc_rules = {
        r.counterparty_key: r.rule
        for r in db.session.query(CounterpartyRule)
        .filter_by(user_pk=user_pk, active=True)
        .all()
    }

    exp_rules = {
        r.counterparty_key: r.rule
        for r in db.session.query(CounterpartyExpenseRule)
        .filter_by(user_pk=user_pk, active=True)
        .all()
    }

    tx_in = (
        db.session.query(Transaction.id, Transaction.counterparty)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )
    for tx_id, cp in tx_in:
        if not cp:
            continue
        rule = inc_rules.get(cp)
        if not rule:
            continue

        label = db.session.query(IncomeLabel).filter_by(transaction_id=tx_id).first()
        if (not label) or (label.status == "unknown"):
            if not label:
                label = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)
            label.status = rule  # income/non_income
            label.confidence = 90
            label.labeled_by = "auto"
            label.decided_at = utcnow()
            db.session.add(label)

    tx_out = (
        db.session.query(Transaction.id, Transaction.counterparty)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )
    for tx_id, cp in tx_out:
        if not cp:
            continue
        rule = exp_rules.get(cp)
        if not rule:
            continue

        label = db.session.query(ExpenseLabel).filter_by(transaction_id=tx_id).first()
        if (not label) or (label.status == "unknown"):
            if not label:
                label = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)
            label.status = rule  # business/personal
            label.confidence = 90
            label.labeled_by = "auto"
            label.decided_at = utcnow()
            db.session.add(label)

    db.session.commit()


def build_planned_by_day(user_pk: int, month_first: date) -> dict:
    start_d = date(month_first.year, month_first.month, 1)
    last_day = calendar.monthrange(month_first.year, month_first.month)[1]
    end_d = date(month_first.year, month_first.month, last_day) + timedelta(days=1)

    rules = (
        db.session.query(RecurringRule)
        .filter(RecurringRule.user_pk == user_pk, RecurringRule.is_active.is_(True))
        .all()
    )

    planned = {}  # date -> list[dict]
    for r in rules:
        if r.start_date and r.start_date >= end_d:
            continue

        if r.cadence == "monthly":
            if not r.day_of_month:
                continue
            day = min(int(r.day_of_month), last_day)
            d = date(month_first.year, month_first.month, day)
            if r.start_date and d < r.start_date:
                continue
            planned.setdefault(d, []).append({
                "id": r.id, "direction": r.direction, "amount_krw": r.amount_krw,
                "counterparty": r.counterparty, "memo": r.memo
            })

        elif r.cadence == "weekly":
            if r.weekday is None:
                continue
            for day_i in range(1, last_day + 1):
                d = date(month_first.year, month_first.month, day_i)
                if r.start_date and d < r.start_date:
                    continue
                if d.weekday() == int(r.weekday):
                    planned.setdefault(d, []).append({
                        "id": r.id, "direction": r.direction, "amount_krw": r.amount_krw,
                        "counterparty": r.counterparty, "memo": r.memo
                    })

    return planned


# -----------------------------
# Views
# -----------------------------
@web_calendar_bp.get("/calendar")
def month_calendar():
    user_pk = _uid()
    month_first = _parse_month(request.args.get("month"))
    start_d, end_d = _month_range(month_first)

    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    today = date.today()

    rows = (
        db.session.query(
            DAY_EXPR.label("d"),
            func.coalesce(func.sum(case((Transaction.direction == "in", Transaction.amount_krw), else_=0)), 0).label("income"),
            func.coalesce(func.sum(case((Transaction.direction == "out", Transaction.amount_krw), else_=0)), 0).label("expense"),
            func.count(Transaction.id).label("cnt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("d")
        .order_by("d")
        .all()
    )

    by_day = {}
    for r in rows:
        by_day[r.d] = {"income": int(r.income), "expense": int(r.expense), "cnt": int(r.cnt)}

    month_income = sum(v["income"] for v in by_day.values())
    month_expense = sum(v["expense"] for v in by_day.values())
    month_net = month_income - month_expense

    volumes = [(v["income"] + v["expense"]) for v in by_day.values()]
    max_vol = max(volumes) if volumes else 0

    heat = {}
    for d0, v in by_day.items():
        vol = v["income"] + v["expense"]
        if max_vol <= 0 or vol <= 0:
            heat[d0] = 0
        else:
            ratio = vol / max_vol
            heat[d0] = 1 if ratio < 0.25 else 2 if ratio < 0.5 else 3 if ratio < 0.8 else 4

    top_out = (
        db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(5)
        .all()
    )

    top_in = (
        db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(5)
        .all()
    )

    prev_month = (month_first - timedelta(days=1)).replace(day=1)
    next_month = end_d
    grid = _calendar_grid(month_first)

    income_need = (
        db.session.query(func.count(Transaction.id))
        .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
        .scalar()
    ) or 0

    expense_need = (
        db.session.query(func.count(Transaction.id))
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status == "unknown"))
        .scalar()
    ) or 0

    evidence_required_missing = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, EvidenceItem.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "required")
        .filter(EvidenceItem.status == "missing")
        .scalar()
    ) or 0

    evidence_maybe_missing = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, EvidenceItem.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "maybe")
        .filter(EvidenceItem.status == "missing")
        .scalar()
    ) or 0

    active_links = (
        db.session.query(func.count(BankAccountLink.id))
        .filter(BankAccountLink.user_pk == user_pk)
        .filter(BankAccountLink.is_active.is_(True))
        .scalar()
    ) or 0

    urls = {
        "bank": _safe_url("web_bank.index") or "/dashboard/bank",
        "inbox": _safe_url("web_inbox.index"),
    }

    month_key = month_first.strftime("%Y-%m")
    planned_by_day = build_planned_by_day(user_pk, month_first)

    snapshot = (
        db.session.query(DashboardSnapshot)
        .filter_by(user_pk=user_pk, month_key=month_key)
        .order_by(DashboardSnapshot.created_at.desc())
        .first()
    )

    return render_template(
        "calendar/month.html",
        month_first=month_first,
        prev_month=prev_month,
        next_month=next_month,
        grid=grid,
        by_day=by_day,
        heat=heat,
        today=today,
        month_income=month_income,
        month_expense=month_expense,
        month_net=month_net,
        top_out=top_out,
        top_in=top_in,
        income_need=int(income_need),
        expense_need=int(expense_need),
        evidence_required_missing=int(evidence_required_missing),
        evidence_maybe_missing=int(evidence_maybe_missing),
        active_links=int(active_links),
        urls=urls,
        month_key=month_key,
        planned_by_day=planned_by_day,
        snapshot=snapshot,
    )


@web_calendar_bp.get("/day/<ymd>")
def day_detail(ymd: str):
    user_pk = _uid()
    d = datetime.strptime(ymd, "%Y-%m-%d").date()

    start_dt = datetime.combine(d, time.min)
    end_dt = start_dt + timedelta(days=1)

    direction = (request.args.get("dir") or "all").strip()
    if direction not in ("all", "in", "out"):
        direction = "all"

    q = (request.args.get("q") or "").strip()

    query = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )

    if direction in ("in", "out"):
        query = query.filter(Transaction.direction == direction)

    if q:
        like = f"%{q}%"
        query = query.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    txs = query.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).all()

    day_income = sum(t.amount_krw for t in txs if t.direction == "in")
    day_expense = sum(t.amount_krw for t in txs if t.direction == "out")
    day_net = day_income - day_expense

    def _top(direction_value: str):
        q2 = (
            db.session.query(
                func.coalesce(Transaction.counterparty, "기타").label("name"),
                func.sum(Transaction.amount_krw).label("sum_amt"),
                func.count(Transaction.id).label("cnt"),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == direction_value)
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .group_by("name")
            .order_by(func.sum(Transaction.amount_krw).desc())
            .limit(5)
            .all()
        )
        return q2

    top_in = _top("in")
    top_out = _top("out")

    prev_day = d - timedelta(days=1)
    next_day = d + timedelta(days=1)

    return render_template(
        "calendar/day.html",
        d=d,
        txs=txs,
        direction=direction,
        q=q,
        day_income=day_income,
        day_expense=day_expense,
        day_net=day_net,
        top_in=top_in,
        top_out=top_out,
        prev_day=prev_day,
        next_day=next_day,
    )


@web_calendar_bp.post("/day/<ymd>/quick-add")
def day_quick_add(ymd: str):
    user_pk = _uid()

    try:
        d = datetime.strptime(ymd, "%Y-%m-%d").date()
    except ValueError:
        flash("날짜 형식이 올바르지 않습니다.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd))

    direction = (request.form.get("direction") or "").strip()
    amount_krw = request.form.get("amount_krw", type=int)
    counterparty = (request.form.get("counterparty") or "").strip() or None
    memo = (request.form.get("memo") or "").strip() or None

    if direction not in ("in", "out"):
        flash("구분을 선택해주세요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd))

    if not amount_krw or amount_krw <= 0:
        flash("금액은 1원 이상 입력해주세요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd))

    tx = Transaction(
        user_pk=user_pk,
        import_job_id=None,
        occurred_at=datetime.combine(d, time.min),
        direction=direction,
        amount_krw=amount_krw,
        counterparty=counterparty,
        memo=memo,
        source="manual",
        external_hash=uuid4().hex,
    )

    try:
        db.session.add(tx)
        db.session.commit()
        flash("거래가 추가되었습니다.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("저장 중 문제가 발생했어요. 다시 시도해주세요.", "error")

    return redirect(url_for("web_calendar.day_detail", ymd=ymd))


@web_calendar_bp.get("/year")
def year_view():
    user_pk = _uid()
    y = int(request.args.get("year") or date.today().year)

    start_dt = datetime(y, 1, 1)
    end_dt = datetime(y + 1, 1, 1)

    month_num = func.extract("month", Transaction.occurred_at)

    rows = (
        db.session.query(
            month_num.label("m"),
            func.coalesce(func.sum(case((Transaction.direction == "in", Transaction.amount_krw), else_=0)), 0).label("income"),
            func.coalesce(func.sum(case((Transaction.direction == "out", Transaction.amount_krw), else_=0)), 0).label("expense"),
            func.count(Transaction.id).label("cnt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("m")
        .order_by("m")
        .all()
    )

    month_map = {int(r.m): {"income": int(r.income), "expense": int(r.expense), "cnt": int(r.cnt)} for r in rows}

    months = []
    for m in range(1, 13):
        inc = month_map.get(m, {}).get("income", 0)
        exp = month_map.get(m, {}).get("expense", 0)
        cnt = month_map.get(m, {}).get("cnt", 0)
        net = inc - exp
        months.append({
            "m": m,
            "month_key": f"{y:04d}-{m:02d}",
            "income": inc,
            "expense": exp,
            "net": net,
            "cnt": cnt,
        })

    year_income = sum(x["income"] for x in months)
    year_expense = sum(x["expense"] for x in months)
    year_net = year_income - year_expense

    non_empty = [m for m in months if (m["income"] or m["expense"])]
    best_month = max(non_empty, key=lambda x: x["net"]) if non_empty else None
    worst_month = min(non_empty, key=lambda x: x["net"]) if non_empty else None
    avg_net = int(year_net / 12)

    top_out = (
        db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(8)
        .all()
    )

    top_in = (
        db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(8)
        .all()
    )

    return render_template(
        "calendar/year.html",
        y=y,
        months=months,
        year_income=year_income,
        year_expense=year_expense,
        year_net=year_net,
        avg_net=avg_net,
        best_month=best_month,
        worst_month=worst_month,
        top_in=top_in,
        top_out=top_out,
    )


@web_calendar_bp.get("/review")
def review():
    user_pk = _uid()

    month_first = _parse_month(request.args.get("month"))
    month_key = month_first.strftime("%Y-%m")

    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    focus = (request.args.get("focus") or "evidence_required").strip()
    if focus not in ("evidence_required", "evidence_maybe", "income_unknown", "expense_unknown"):
        focus = "evidence_required"

    q = (request.args.get("q") or "").strip()
    limit = _parse_limit(request.args.get("limit"), default=200)

    base = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )
    if q:
        like = f"%{q}%"
        base = base.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    # --- 탭 카운트 (항상 템플릿에서 필요) ---
    income_need = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "in")
        .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
        .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
    ).scalar() or 0

    expense_need = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "out")
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status == "unknown"))
    ).scalar() or 0

    evidence_required_missing = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(EvidenceItem.requirement == "required")
        .filter(EvidenceItem.status == "missing")
    ).scalar() or 0

    evidence_maybe_missing = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(EvidenceItem.requirement == "maybe")
        .filter(EvidenceItem.status == "missing")
    ).scalar() or 0

    counts = {
        "evidence_required": int(evidence_required_missing or 0),
        "evidence_maybe": int(evidence_maybe_missing or 0),
        "income_unknown": int(income_need or 0),
        "expense_unknown": int(expense_need or 0),
    }

    # --- Focus별 목록 조회 ---
    rows = []
    items = []
    title = ""

    if focus == "income_unknown":
        title = "수입 분류 필요"
        query = (
            base.filter(Transaction.direction == "in")
            .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
            .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
            .with_entities(Transaction, IncomeLabel.status, IncomeLabel.confidence, IncomeLabel.labeled_by)
            .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        )
        rows = query.limit(limit).all()
        for tx, status, conf, by in rows:
            items.append({
                "tx": tx,
                "need_label": True,
                "need_evidence": False,
                "label_status": status or "unknown",
                "confidence": int(conf or 0),
                "labeled_by": by or "auto",
            })

    elif focus == "expense_unknown":
        title = "지출 분류 필요"
        query = (
            base.filter(Transaction.direction == "out")
            .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
            .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status == "unknown"))
            .with_entities(Transaction, ExpenseLabel.status, ExpenseLabel.confidence, ExpenseLabel.labeled_by)
            .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        )
        rows = query.limit(limit).all()
        for tx, status, conf, by in rows:
            items.append({
                "tx": tx,
                "need_label": True,
                "need_evidence": False,
                "label_status": status or "unknown",
                "confidence": int(conf or 0),
                "labeled_by": by or "auto",
            })

    elif focus == "evidence_required":
        title = "필수 영수증 미첨부"
        query = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "required")
            .filter(EvidenceItem.status == "missing")
            .with_entities(Transaction, EvidenceItem.requirement, EvidenceItem.status)
            .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        )
        rows = query.limit(limit).all()
        for tx, req, st in rows:
            items.append({
                "tx": tx,
                "need_label": False,
                "need_evidence": True,
                "requirement": req,
                "evidence_status": st,
            })

    else:  # evidence_maybe
        title = "검토 영수증 미첨부"
        query = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "maybe")
            .filter(EvidenceItem.status == "missing")
            .with_entities(Transaction, EvidenceItem.requirement, EvidenceItem.status)
            .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        )
        rows = query.limit(limit).all()
        for tx, req, st in rows:
            items.append({
                "tx": tx,
                "need_label": False,
                "need_evidence": True,
                "requirement": req,
                "evidence_status": st,
            })

    return render_template(
        "calendar/review.html",
        month_first=month_first,
        month_key=month_key,
        focus=focus,
        title=title,
        q=q,
        limit=limit,

        evidence_required_missing=evidence_required_missing,
        evidence_maybe_missing=evidence_maybe_missing,
        income_need=income_need,
        expense_need=expense_need,
        counts=counts,
        totals=counts,

        items=items,
        rows=rows,  # 기존 호환
    )


@web_calendar_bp.post("/review/income/<int:tx_id>")
def review_set_income(tx_id: int):
    user_pk = _uid()
    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        flash("거래를 찾을 수 없어요.", "error")
        return redirect(url_for("web_calendar.month_calendar"))

    status = (request.form.get("status") or "").strip()  # income / non_income
    if status not in ("income", "non_income"):
        flash("처리값이 올바르지 않아요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

    always = (request.form.get("always") == "1")
    focus = request.form.get("focus") or "income_unknown"
    q = request.form.get("q") or ""
    month_key = request.form.get("month") or _month_key_from_tx(tx)
    limit = _parse_limit(request.form.get("limit"), default=200)

    label = (
        db.session.query(IncomeLabel)
        .filter_by(user_pk=user_pk, transaction_id=tx_id)
        .first()
    )
    if not label:
        label = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = utcnow()

    if always:
        key = _cp_key(tx.counterparty)
        if key:
            rule = (
                db.session.query(CounterpartyRule)
                .filter_by(user_pk=user_pk, counterparty_key=key)
                .first()
            )
            if not rule:
                rule = CounterpartyRule(user_pk=user_pk, counterparty_key=key)
            rule.rule = status
            rule.active = True
            db.session.add(rule)

    try:
        db.session.add(label)
        db.session.commit()
        flash("처리 완료", "success")
    except IntegrityError:
        db.session.rollback()
        flash("저장 중 문제가 발생했어요.", "error")

    # ✅ 다음 항목으로 자동 이동
    month_first = _parse_month(month_key)
    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)
    next_id = _next_review_tx_id(user_pk=user_pk, focus=focus, start_dt=start_dt, end_dt=end_dt, q=q, after_tx=tx)

    return _back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)


@web_calendar_bp.post("/review/expense/<int:tx_id>")
def review_set_expense(tx_id: int):
    user_pk = _uid()
    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        flash("거래를 찾을 수 없어요.", "error")
        return redirect(url_for("web_calendar.month_calendar"))

    status = (request.form.get("status") or "").strip()  # business / personal / mixed
    if status not in ("business", "personal", "mixed"):
        flash("처리값이 올바르지 않아요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

    always = (request.form.get("always") == "1")
    focus = request.form.get("focus") or "expense_unknown"
    q = request.form.get("q") or ""
    month_key = request.form.get("month") or _month_key_from_tx(tx)
    limit = _parse_limit(request.form.get("limit"), default=200)

    label = (
        db.session.query(ExpenseLabel)
        .filter_by(user_pk=user_pk, transaction_id=tx_id)
        .first()
    )
    if not label:
        label = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = utcnow()

    if always and status in ("business", "personal"):
        key = _cp_key(tx.counterparty)
        if key:
            rule = (
                db.session.query(CounterpartyExpenseRule)
                .filter_by(user_pk=user_pk, counterparty_key=key)
                .first()
            )
            if not rule:
                rule = CounterpartyExpenseRule(user_pk=user_pk, counterparty_key=key)
            rule.rule = status
            rule.active = True
            db.session.add(rule)

    try:
        db.session.add(label)
        db.session.commit()
        flash("처리 완료", "success")
    except IntegrityError:
        db.session.rollback()
        flash("저장 중 문제가 발생했어요.", "error")

    month_first = _parse_month(month_key)
    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)
    next_id = _next_review_tx_id(user_pk=user_pk, focus=focus, start_dt=start_dt, end_dt=end_dt, q=q, after_tx=tx)

    return _back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)


@web_calendar_bp.post("/review/evidence/<int:tx_id>")
def review_set_evidence(tx_id: int):
    user_pk = _uid()
    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        flash("거래를 찾을 수 없어요.", "error")
        return redirect(url_for("web_calendar.month_calendar"))

    action = (request.form.get("action") or "").strip()  # attached / not_needed / missing
    if action not in ("attached", "not_needed", "missing"):
        flash("처리값이 올바르지 않아요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

    focus = request.form.get("focus") or "evidence_required"
    q = request.form.get("q") or ""
    month_key = request.form.get("month") or _month_key_from_tx(tx)
    limit = _parse_limit(request.form.get("limit"), default=200)

    item = (
        db.session.query(EvidenceItem)
        .filter_by(user_pk=user_pk, transaction_id=tx_id)
        .first()
    )
    if not item:
        item = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement="maybe", status="missing")

    if action == "attached":
        item.status = "attached"
    elif action == "not_needed":
        item.requirement = "not_needed"
        item.status = "not_needed"
    else:
        item.status = "missing"

    try:
        db.session.add(item)
        db.session.commit()
        flash("처리 완료", "success")
    except IntegrityError:
        db.session.rollback()
        flash("저장 중 문제가 발생했어요.", "error")

    month_first = _parse_month(month_key)
    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)
    next_id = _next_review_tx_id(user_pk=user_pk, focus=focus, start_dt=start_dt, end_dt=end_dt, q=q, after_tx=tx)

    return _back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)


# ---- 아래 3개는 기존 호환용(남겨둠) ----
@web_calendar_bp.post("/label/income")
def set_income_label():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "unknown").strip()
    next_url = request.form.get("next") or url_for("web_calendar.month_calendar")

    if status not in ("income", "non_income", "unknown"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    label = db.session.query(IncomeLabel).filter_by(transaction_id=tx_id).first()
    if not label:
        label = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = datetime.now(timezone.utc)

    db.session.add(label)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/label/expense")
def set_expense_label():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "unknown").strip()
    next_url = request.form.get("next") or url_for("web_calendar.month_calendar")

    if status not in ("business", "personal", "mixed", "unknown"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    label = db.session.query(ExpenseLabel).filter_by(transaction_id=tx_id).first()
    if not label:
        label = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = datetime.now(timezone.utc)

    db.session.add(label)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/evidence")
def set_evidence_status():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "missing").strip()
    next_url = request.form.get("next") or url_for("web_calendar.month_calendar")

    if status not in ("missing", "attached", "not_needed"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    item = db.session.query(EvidenceItem).filter_by(transaction_id=tx_id).first()
    if not item:
        item = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement="maybe", status="missing")

    item.status = status
    item.updated_at = datetime.now(timezone.utc)

    db.session.add(item)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/month-close")
def month_close():
    user_pk = _uid()
    month_first = _parse_month(request.form.get("month"))
    month_key = month_first.strftime("%Y-%m")
    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    income_total = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk, Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    expense_total = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk, Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    exp_rows = (
        db.session.query(
            func.coalesce(ExpenseLabel.status, "unknown").label("st"),
            func.coalesce(func.sum(Transaction.amount_krw), 0).label("sum_amt"),
        )
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk, Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by("st")
        .all()
    )
    exp_sum = {r.st: int(r.sum_amt) for r in exp_rows}

    inc_unknown_cnt = (
        db.session.query(func.count(Transaction.id))
        .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk, Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
        .scalar()
    ) or 0

    exp_unknown_cnt = (
        db.session.query(func.count(Transaction.id))
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk, Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status == "unknown"))
        .scalar()
    ) or 0

    ev_required_missing = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, EvidenceItem.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "required", EvidenceItem.status == "missing")
        .scalar()
    ) or 0

    payload = {
        "month_key": month_key,
        "income_total": int(income_total),
        "expense_total": int(expense_total),
        "net": int(income_total) - int(expense_total),
        "expense_business": exp_sum.get("business", 0),
        "expense_personal": exp_sum.get("personal", 0),
        "expense_mixed": exp_sum.get("mixed", 0),
        "expense_unknown": exp_sum.get("unknown", 0),
        "income_unknown_cnt": int(inc_unknown_cnt),
        "expense_unknown_cnt": int(exp_unknown_cnt),
        "evidence_required_missing": int(ev_required_missing),
        "closed_at": utcnow().isoformat(),
    }

    db.session.query(DashboardSnapshot).filter_by(user_pk=user_pk, month_key=month_key).delete()
    db.session.add(DashboardSnapshot(user_pk=user_pk, month_key=month_key, payload=payload))
    db.session.commit()

    return redirect(url_for("web_calendar.month_calendar", month=month_key))


@web_calendar_bp.get("/tax-buffer")
def tax_buffer():
    user_pk = _uid()

    month_first = _parse_month(request.args.get("month"))
    month_key = month_first.strftime("%Y-%m")

    # ✅ overview/inbox와 같은 기준(설정 세율 + 동일 월경계)으로 계산
    r = compute_risk_summary(user_pk, month_key=month_key)

    # 화면 표시용 tax_rate(퍼센트 표기)
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()

    tax_rate = float(s.default_tax_rate or 0.15)
    if tax_rate > 1:
        tax_rate = tax_rate / 100.0
    tax_rate = max(0.0, min(tax_rate, 0.95))

    income_total = int(r.gross_income_krw)
    recommended = int(r.buffer_target_krw)
    balance = int(r.buffer_total_krw)

    ledger = (
        db.session.query(TaxBufferLedger)
        .filter(TaxBufferLedger.user_pk == user_pk)
        .order_by(TaxBufferLedger.created_at.desc(), TaxBufferLedger.id.desc())
        .limit(40)
        .all()
    )

    progress_pct = 0
    if recommended > 0:
        progress_pct = int(min(100, max(0, (balance / recommended) * 100)))
    shortage = max(0, recommended - balance)
    overage = max(0, balance - recommended)

    return render_template(
        "calendar/tax_buffer.html",
        month_key=month_key,
        month_first=month_first,
        tax_rate=tax_rate,
        income_total=income_total,
        recommended=recommended,
        balance=balance,
        gap=shortage,
        ledger=ledger,
        progress_pct=progress_pct,
        shortage=shortage,
        overage=overage,
    )



@web_calendar_bp.post("/tax-buffer/adjust")
def tax_buffer_adjust():
    user_pk = _uid()
    delta = int(request.form.get("delta") or 0)
    note = (request.form.get("note") or "").strip()
    next_url = request.form.get("next") or url_for("web_calendar.tax_buffer")

    if delta == 0:
        return redirect(next_url)

    db.session.add(TaxBufferLedger(user_pk=user_pk, delta_amount_krw=delta, note=note))
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.get("/tx/new")
def tx_new():
    user_pk = _uid()

    month_key = (request.args.get("month") or "").strip()
    ymd = (request.args.get("date") or "").strip()  # YYYY-MM-DD

    if ymd:
        default_date = ymd
    elif month_key:
        default_date = f"{month_key}-01"
    else:
        default_date = date.today().strftime("%Y-%m-%d")

    if not month_key and default_date:
        month_key = default_date[:7]

    recent = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(8)
        .all()
    )

    next_url = (request.args.get("next") or "").strip()
    if not next_url:
        next_url = (
            url_for("web_calendar.month_calendar", month=month_key)
            if month_key
            else url_for("web_calendar.month_calendar")
        )

    return render_template(
        "calendar/tx_new.html",
        month_key=month_key,
        default_date=default_date,
        recent=recent,
        next_url=next_url,
    )


@web_calendar_bp.post("/tx/new")
def tx_create():
    user_pk = _uid()

    ymd = request.form.get("date")  # YYYY-MM-DD
    hhmm = request.form.get("time") or "12:00"
    direction = (request.form.get("direction") or "out").strip()
    amount = int(request.form.get("amount_krw") or 0)
    counterparty = (request.form.get("counterparty") or "").strip() or None
    memo = (request.form.get("memo") or "").strip() or None
    next_url = request.form.get("next") or url_for("web_calendar.month_calendar")

    if direction not in ("in", "out") or amount <= 0 or not ymd:
        return redirect(next_url)

    occurred_at = datetime.strptime(f"{ymd} {hhmm}", "%Y-%m-%d %H:%M")

    raw = f"manual:{user_pk}:{occurred_at.isoformat()}:{direction}:{amount}:{counterparty or ''}:{memo or ''}"
    external_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    tx = Transaction(
        user_pk=user_pk,
        import_job_id=None,
        occurred_at=occurred_at,
        direction=direction,
        amount_krw=amount,
        counterparty=counterparty,
        memo=memo,
        source="manual",
        external_hash=external_hash,
    )
    db.session.add(tx)
    db.session.commit()

    return redirect(next_url)


@web_calendar_bp.get("/search")
def month_search():
    user_pk = _uid()
    month_first = _parse_month(request.args.get("month"))
    q = (request.args.get("q") or "").strip()

    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    query = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )

    if q:
        like = f"%{q}%"
        query = query.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    txs = query.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).limit(300).all()

    groups = {}
    for t in txs:
        d0 = t.occurred_at.date()
        groups.setdefault(d0, []).append(t)

    return render_template(
        "calendar/search.html",
        month_first=month_first,
        q=q,
        groups=sorted(groups.items(), key=lambda x: x[0], reverse=True),
    )


@web_calendar_bp.get("/recurring")
def recurring_list():
    user_pk = _uid()
    rules = (
        db.session.query(RecurringRule)
        .filter(RecurringRule.user_pk == user_pk)
        .order_by(RecurringRule.is_active.desc(), RecurringRule.id.desc())
        .all()
    )
    return render_template("calendar/recurring_list.html", rules=rules)


@web_calendar_bp.post("/recurring/create")
def recurring_create():
    user_pk = _uid()
    direction = request.form.get("direction") or "out"
    amount = int(request.form.get("amount_krw") or 0)
    cadence = request.form.get("cadence") or "monthly"
    day_of_month = request.form.get("day_of_month")
    weekday = request.form.get("weekday")
    counterparty = (request.form.get("counterparty") or "").strip() or None
    memo = (request.form.get("memo") or "").strip() or None

    rr = RecurringRule(
        user_pk=user_pk,
        direction=direction if direction in ("in", "out") else "out",
        amount_krw=max(0, amount),
        cadence=cadence if cadence in ("monthly", "weekly") else "monthly",
        day_of_month=int(day_of_month) if day_of_month else None,
        weekday=int(weekday) if weekday else None,
        counterparty=counterparty,
        memo=memo,
        start_date=utcnow().date(),
        is_active=True,
    )
    db.session.add(rr)
    db.session.commit()
    return redirect(url_for("web_calendar.recurring_list"))


@web_calendar_bp.post("/recurring/toggle")
def recurring_toggle():
    user_pk = _uid()
    rid = int(request.form.get("id") or 0)
    rr = db.session.query(RecurringRule).filter_by(id=rid, user_pk=user_pk).first()
    if rr:
        rr.is_active = not rr.is_active
        db.session.commit()
    return redirect(url_for("web_calendar.recurring_list"))
