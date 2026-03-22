# routes/web_calendar.py
from __future__ import annotations

from datetime import date, datetime, timedelta, time, timezone
import calendar
import hashlib
from uuid import uuid4

from flask import Blueprint, render_template, request, session, url_for, current_app, redirect, flash
from sqlalchemy import func, case, cast, Date, or_, and_
from werkzeug.exceptions import Unauthorized
from sqlalchemy.exc import IntegrityError

from core.extensions import db
from domain.models import (
    Transaction, IncomeLabel, ExpenseLabel, EvidenceItem,
    CounterpartyRule, CounterpartyExpenseRule,
    DashboardSnapshot, SafeToSpendSettings, TaxBufferLedger,
    OfficialDataDocument, ReferenceMaterialItem,
    BankAccountLink, RecurringRule
)
from routes.web.vault import _ensure_month_evidence_rows

from services.onboarding import build_onboarding_reflection
from services.risk import compute_risk_summary
from services.transaction_origin import get_transaction_badge_label
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


def _review_profile_guidance(user_pk: int) -> dict[str, object]:
    profile = build_onboarding_reflection(user_pk)
    title = "현재 설정 정보가 없어 기본 정리 순서를 먼저 보여드리고 있습니다."
    items = [
        "필수 영수증과 분류부터 먼저 정리한 뒤, 공식자료와 참고자료를 순서대로 보강해 주세요.",
    ]

    if profile["is_freelancer"]:
        title = "입력하신 정보 기준으로는 프리랜서 자료를 먼저 맞추는 편이 좋습니다."
        items = [
            "원천징수 관련 문서와 홈택스 납부내역을 먼저 챙기면 정리 결과를 세무 자료와 연결하기 쉽습니다.",
        ]
    elif profile["is_business_owner"]:
        title = "입력하신 정보 기준으로는 사업자용 자료를 먼저 나눠 두는 편이 좋습니다."
        items = [
            "사업 관련 지출을 먼저 정리한 뒤 공식자료 업로드로 넘어가면 전달 흐름이 덜 꼬입니다.",
        ]
    elif profile["is_employee_sidejob"]:
        title = "입력하신 정보 기준으로는 본업과 부업 자료를 나눠 정리하는 편이 좋습니다."
        items = [
            "원천징수 문서와 부업 관련 지출/증빙을 섞지 않게 먼저 분리해 두세요.",
        ]

    if profile["is_vat_business"]:
        items.append("과세사업자/부가세 대상이면 이번 달 지출 정리 후 공식자료에서 납부내역을 먼저 확인해 두는 편이 안전합니다.")
    if profile["is_local_insured"]:
        items.append("지역가입자 기준이라면 건보 납부확인서와 자격 관련 문서를 같이 준비해 두세요.")
    elif profile["is_employee_insured"] and profile["is_employee_sidejob"]:
        items.append("직장가입자 + 부업이면 부업 자료와 건보 관련 참고자료를 따로 남겨 두면 설명이 쉬워집니다.")

    return {
        "review_profile_title": title,
        "review_profile_items": items,
        "review_profile_has_specific": profile["has_any_specific"],
    }


def _tax_buffer_profile_guidance(user_pk: int) -> dict[str, object]:
    profile = build_onboarding_reflection(user_pk)
    title = "현재 설정 정보가 없어 기본 해석 가이드를 먼저 보여드리고 있습니다."
    items = [
        "세금 보관함 숫자는 우선 참고용으로 보고, 거래 정리와 공식자료 보강을 같이 진행해 주세요.",
    ]

    if profile["is_freelancer"]:
        title = "입력하신 정보 기준으로는 프리랜서용 세금/건보 자료를 같이 보는 편이 좋습니다."
        items = [
            "원천징수 관련 문서와 홈택스 납부내역을 같이 보면 보관 금액 해석이 쉬워집니다.",
        ]
    elif profile["is_business_owner"]:
        title = "입력하신 정보 기준으로는 사업자용 자료를 같이 보며 해석하는 편이 좋습니다."
        items = [
            "사업 관련 지출과 공식자료를 먼저 맞춘 뒤 보관 금액을 보면 덜 헷갈립니다.",
        ]
    elif profile["is_employee_sidejob"]:
        title = "입력하신 정보 기준으로는 본업과 부업을 섞지 않고 보는 편이 좋습니다."
        items = [
            "직장인 + 부업이면 세금 보관함 숫자를 확정값처럼 보기보다, 본업/부업 자료를 나눠 확인해 주세요.",
        ]

    if profile["is_vat_business"]:
        items.append("과세사업자/부가세 대상이면 부가세 관련 자료 준비를 더 빨리 시작하는 편이 안전합니다.")
    if profile["is_local_insured"]:
        items.append("지역가입자 기준이라면 건보 납부확인 자료를 같이 보면 부족분 해석이 쉬워집니다.")
    elif profile["is_employee_insured"]:
        items.append("직장가입자 기준이라면 건보 자료는 예외 상황 확인용으로만 보수적으로 참고해 주세요.")

    return {
        "tax_buffer_profile_title": title,
        "tax_buffer_profile_items": items,
        "tax_buffer_profile_has_specific": profile["has_any_specific"],
    }

# -----------------------------
# Export: 세무사 전달 패키지 (ZIP)
# -----------------------------
@web_calendar_bp.get("/tax-package")
def tax_package():
    month_key = (request.args.get("month") or "").strip()
    profile_code = (request.args.get("profile") or "").strip()
    values = {"month": month_key}
    if profile_code:
        values["profile"] = profile_code
    return redirect(url_for("web_package.download", **values))


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
        tx_source_badge_label=get_transaction_badge_label,
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


@web_calendar_bp.get("/reconcile")
def reconcile():
    user_pk = _uid()

    month_first = _parse_month(request.args.get("month"))
    month_key = month_first.strftime("%Y-%m")

    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    _ensure_month_evidence_rows(user_pk=user_pk, start_dt=start_dt, end_dt=end_dt)

    prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
    next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1).strftime("%Y-%m")

    tx_total = (
        db.session.query(func.count(Transaction.id))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

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

    income_unknown_count = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "in")
        .filter(or_(IncomeLabel.id.is_(None), IncomeLabel.status == "unknown"))
        .scalar()
    ) or 0

    expense_unknown_count = (
        db.session.query(func.count(func.distinct(Transaction.id)))
        .select_from(Transaction)
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.direction == "out")
        .filter(or_(ExpenseLabel.id.is_(None), ExpenseLabel.status == "unknown"))
        .scalar()
    ) or 0

    missing_required = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "required")
        .filter(EvidenceItem.status == "missing")
        .scalar()
    ) or 0

    missing_maybe = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.requirement == "maybe")
        .filter(EvidenceItem.status == "missing")
        .scalar()
    ) or 0

    attached_total = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(EvidenceItem.status == "attached")
        .scalar()
    ) or 0

    official_total = (
        db.session.query(func.count(OfficialDataDocument.id))
        .filter(OfficialDataDocument.user_pk == user_pk)
        .scalar()
    ) or 0

    official_parsed = (
        db.session.query(func.count(OfficialDataDocument.id))
        .filter(OfficialDataDocument.user_pk == user_pk)
        .filter(OfficialDataDocument.parse_status == "parsed")
        .scalar()
    ) or 0

    official_needs_review = (
        db.session.query(func.count(OfficialDataDocument.id))
        .filter(OfficialDataDocument.user_pk == user_pk)
        .filter(OfficialDataDocument.parse_status == "needs_review")
        .scalar()
    ) or 0

    reference_total = (
        db.session.query(func.count(ReferenceMaterialItem.id))
        .filter(ReferenceMaterialItem.user_pk == user_pk)
        .scalar()
    ) or 0

    reference_files = (
        db.session.query(func.count(ReferenceMaterialItem.id))
        .filter(ReferenceMaterialItem.user_pk == user_pk)
        .filter(ReferenceMaterialItem.material_kind == "reference")
        .scalar()
    ) or 0

    note_attachments = (
        db.session.query(func.count(ReferenceMaterialItem.id))
        .filter(ReferenceMaterialItem.user_pk == user_pk)
        .filter(ReferenceMaterialItem.material_kind == "note_attachment")
        .scalar()
    ) or 0

    missing_total = int(missing_required or 0) + int(missing_maybe or 0)
    classification_total = int(income_unknown_count or 0) + int(expense_unknown_count or 0)
    evidence_denom = int(attached_total or 0) + int(missing_total or 0)
    evidence_ready_pct = 100 if evidence_denom == 0 else int((int(attached_total or 0) * 100) / evidence_denom)
    evidence_state_label = "증빙 확인 필요 없음" if missing_total == 0 else f"확인 필요 {missing_total}건"
    official_state_label = "공식자료 없음" if int(official_total or 0) == 0 else f"보관 {int(official_total)}건"
    reference_state_label = "참고자료 없음" if int(reference_total or 0) == 0 else f"보관 {int(reference_total)}건"

    action_items: list[dict[str, str]] = []
    if int(missing_required) > 0:
        action_items.append(
            {
                "title": "필수 증빙부터 다시 확인하세요",
                "desc": f"필수 영수증 {int(missing_required)}건이 비어 있습니다. 이 항목부터 정리하면 월 마감이 가장 빨라집니다.",
                "href": url_for("web_calendar.review", month=month_key, focus="evidence_required"),
                "cta": "정리하기",
            }
        )
    if classification_total > 0:
        action_items.append(
            {
                "title": "수입·지출 분류를 정리하세요",
                "desc": f"분류가 남은 거래가 {classification_total}건 있습니다. 세금 보관과 패키지 해석 전에 먼저 방향을 정리하는 편이 안전합니다.",
                "href": url_for("web_calendar.review", month=month_key, focus="expense_unknown"),
                "cta": "분류 보기",
            }
        )
    if int(official_total) == 0:
        action_items.append(
            {
                "title": "공식자료를 보강해 두세요",
                "desc": "월 기준 자동 대사는 아직 없지만, 납부내역·원천징수·건보 자료를 모아두면 설명 근거가 훨씬 분명해집니다.",
                "href": url_for("web_official_data.index"),
                "cta": "공식자료 업로드",
            }
        )
    elif int(official_needs_review) > 0:
        action_items.append(
            {
                "title": "검토 필요한 공식자료를 확인하세요",
                "desc": f"현재 검토가 더 필요한 공식자료가 {int(official_needs_review)}건 있습니다. 구조 확인 후 패키지에 포함하는 편이 안전합니다.",
                "href": url_for("web_official_data.index"),
                "cta": "공식자료 보기",
            }
        )
    if int(reference_total) == 0:
        action_items.append(
            {
                "title": "설명이 필요한 자료는 참고자료로 남겨두세요",
                "desc": "자유형 메모나 별도 설명 파일은 참고자료 채널에 분리 보관하면 세무사 전달 준비가 쉬워집니다.",
                "href": url_for("web_reference_material.index"),
                "cta": "참고자료 업로드",
            }
        )
    if not action_items:
        action_items.append(
            {
                "title": "세무사 전달 준비 상태를 확인하세요",
                "desc": "증빙과 자료 채널이 어느 정도 정리됐다면 패키지 화면에서 이번 달 전달 준비 상태를 한 번 더 점검하세요.",
                "href": url_for("web_package.page", month=month_key),
                "cta": "세무사 패키지",
            }
        )

    return render_template(
        "calendar/reconcile.html",
        month_key=month_key,
        month_first=month_first,
        prev_month=prev_month,
        next_month=next_month,
        has_transactions=bool(int(tx_total or 0) > 0),
        tx_total=int(tx_total or 0),
        gross_income=int(gross_income or 0),
        total_out=int(total_out or 0),
        missing_required=int(missing_required or 0),
        missing_maybe=int(missing_maybe or 0),
        missing_total=int(missing_total or 0),
        attached_total=int(attached_total or 0),
        classification_total=int(classification_total or 0),
        income_unknown_count=int(income_unknown_count or 0),
        expense_unknown_count=int(expense_unknown_count or 0),
        official_total=int(official_total or 0),
        official_parsed=int(official_parsed or 0),
        official_needs_review=int(official_needs_review or 0),
        reference_total=int(reference_total or 0),
        reference_files=int(reference_files or 0),
        note_attachments=int(note_attachments or 0),
        evidence_ready_pct=int(evidence_ready_pct),
        evidence_state_label=evidence_state_label,
        official_state_label=official_state_label,
        reference_state_label=reference_state_label,
        action_items=action_items[:4],
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
        **_review_profile_guidance(user_pk),
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
        **_tax_buffer_profile_guidance(user_pk),
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
