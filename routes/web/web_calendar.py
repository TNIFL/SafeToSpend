# routes/web_calendar.py
from __future__ import annotations

from datetime import date, datetime, timedelta, time
import calendar
import hashlib
from uuid import uuid4
import json
import re

from flask import Blueprint, render_template, request, session, url_for, redirect, flash
from sqlalchemy import func, case, cast, Date, or_, and_
from werkzeug.exceptions import Unauthorized
from sqlalchemy.exc import IntegrityError

from core.extensions import db
from core.security import sanitize_next_url
from core.time import utcnow as _now_kst_naive
from domain.models import (
    Transaction, IncomeLabel, ExpenseLabel, EvidenceItem,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    CounterpartyRule, CounterpartyExpenseRule,
    DashboardSnapshot, TaxBufferLedger,
    BankAccountLink, RecurringRule, ActionLog, UserBankAccount
)

from services.risk import compute_tax_estimate
from services.health_insurance import infer_nhi_for_month
from services.onboarding import get_primary_goal, pick_focus_from_counts
from services.bank_accounts import (
    ensure_manual_bucket,
    get_linked_account_balances,
    list_accounts_for_ui,
)
from services.evidence_vault import (
    default_retention_until,
    delete_physical_file,
    resolve_file_path,
    store_evidence_file_multi,
    store_evidence_text_file,
)
from services.input_sanitize import clamp_int, parse_int_krw, safe_str
from services.receipt_parser import parse_receipt_from_file, parse_receipt_from_text
from routes.web.calendar._shared import (
    calendar_grid as _calendar_grid,
    cp_key as _cp_key,
    evidence_defaults_from_expense_status as _evidence_defaults_from_expense_status,
    month_range as _month_range,
    parse_month as _parse_month,
    safe_url as _safe_url,
)
from routes.web.calendar.review import (
    REVIEW_FOCUS,
    DEFAULT_REVIEW_FOCUS,
    parse_limit as _parse_limit,
    register_review_routes,
)
from routes.web.calendar.tax import register_tax_routes
from routes.web.calendar.vault import register_vault_routes
from routes.web.calendar.receipt import register_receipt_routes


web_calendar_bp = Blueprint("web_calendar", __name__, url_prefix="/dashboard")

RECEIPT_EFFECT_QUERY_KEYS = (
    "receipt_effect_event",
    "receipt_effect_level",
    "current_tax_due_est_krw",
    "current_buffer_target_krw",
    "tax_delta_from_receipts_krw",
    "buffer_delta_from_receipts_krw",
    "receipt_reflected_expense_krw",
    "receipt_pending_expense_krw",
    "tax_before",
    "tax_after",
    "buffer_before",
    "buffer_after",
    "expense_before",
    "expense_after",
    "profit_before",
    "profit_after",
)


# -----------------------------
# Utils
# -----------------------------
def utcnow():
    return _now_kst_naive()

def _is_partial() -> bool:
    """모달/부분 렌더링용.
    - GET: ?partial=1
    - POST: hidden input name="partial" value="1"
    """
    try:
        if (request.args.get("partial") or "").strip() == "1":
            return True
        if (request.form.get("partial") or "").strip() == "1":
            return True
    except Exception:
        pass
    return False


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _parse_account_scope(user_pk: int, raw: str | None) -> tuple[str, int | None, str]:
    token = (raw or "all").strip().lower()
    if not token or token == "all":
        return "all", None, "all"
    if token == "unassigned":
        return "unassigned", None, "unassigned"
    try:
        candidate_id = int(token)
    except Exception:
        return "all", None, "all"
    if candidate_id <= 0:
        return "all", None, "all"
    try:
        owned = (
            db.session.query(UserBankAccount.id)
            .filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.id == int(candidate_id))
            .first()
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return "all", None, "all"
    if not owned:
        return "all", None, "all"
    return "account", int(candidate_id), str(candidate_id)


def _apply_account_scope(query, scope_mode: str, scope_account_id: int | None):
    if scope_mode == "account" and scope_account_id:
        return query.filter(Transaction.bank_account_id == int(scope_account_id))
    if scope_mode == "unassigned":
        return query.filter(Transaction.bank_account_id.is_(None))
    return query


def _resolve_account_theme(
    *,
    account_options: list[dict],
    scope_mode: str,
    scope_account_id: int | None,
    scope_value: str,
) -> dict:
    if scope_mode == "account" and scope_account_id:
        selected = next((x for x in account_options if int(x.get("id") or 0) == int(scope_account_id)), None)
        if selected:
            return {
                "mode": "account",
                "value": scope_value,
                "name": selected.get("display_name") or "선택 계좌",
                "color_hex": selected.get("color_hex") or "#2563EB",
            }
    if scope_mode == "unassigned":
        return {"mode": "unassigned", "value": "unassigned", "name": "미지정 거래", "color_hex": "#64748B"}
    return {"mode": "all", "value": "all", "name": "전체 계좌", "color_hex": ""}


def _parse_post_account_id(user_pk: int, raw: str | None, *, allow_blank: bool = True) -> int | None:
    token = (raw or "").strip()
    if not token:
        return None if allow_blank else 0
    try:
        account_id = int(token)
    except Exception:
        return None if allow_blank else 0
    if account_id <= 0:
        return None if allow_blank else 0
    try:
        owned = (
            db.session.query(UserBankAccount.id)
            .filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.id == int(account_id))
            .first()
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None if allow_blank else 0
    if not owned:
        return None if allow_blank else 0
    return int(account_id)


def _receipt_effect_nav_params() -> dict[str, str]:
    params: dict[str, str] = {}
    for key in RECEIPT_EFFECT_QUERY_KEYS:
        raw = request.args.get(key)
        if raw is not None and str(raw).strip() != "":
            params[key] = str(raw)
    return params


def _receipt_effect_int_arg(name: str) -> int | None:
    try:
        raw = request.args.get(name)
        return int(raw) if raw is not None and raw != "" else None
    except Exception:
        return None


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


register_tax_routes(
    bp=web_calendar_bp,
    uid_getter=_uid,
    parse_month=_parse_month,
    compute_tax_estimate=compute_tax_estimate,
    db=db,
    TaxBufferLedger=TaxBufferLedger,
)
register_vault_routes(bp=web_calendar_bp)
register_receipt_routes(
    bp=web_calendar_bp,
    uid_getter=_uid,
    parse_month=_parse_month,
    parse_limit=_parse_limit,
    is_partial=_is_partial,
    review_focus=REVIEW_FOCUS,
    default_review_focus=DEFAULT_REVIEW_FOCUS,
    db=db,
    compute_tax_estimate=compute_tax_estimate,
    utcnow_fn=utcnow,
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


def _apply_income_rule_this_month(
    *,
    user_pk: int,
    counterparty_key: str,
    status: str,  # income/non_income
    start_dt: datetime,
    end_dt: datetime,
    exclude_tx_id: int,
) -> int:
    # 같은 거래처 + 같은 월 범위의 입금 거래 중, user가 확정한 건은 건드리지 않음
    tx_ids = [
        r[0]
        for r in (
            db.session.query(Transaction.id)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "in")
            .filter(Transaction.counterparty == counterparty_key)
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(Transaction.id != exclude_tx_id)
            .all()
        )
    ]
    if not tx_ids:
        return 0

    labels = (
        db.session.query(IncomeLabel)
        .filter(IncomeLabel.user_pk == user_pk)
        .filter(IncomeLabel.transaction_id.in_(tx_ids))
        .all()
    )
    label_map = {l.transaction_id: l for l in labels}

    updated = 0
    for tx_id in tx_ids:
        lab = label_map.get(tx_id)
        if lab and lab.labeled_by == "user":
            continue  # 사용자가 이미 확정한 건은 존중

        if not lab:
            lab = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)

        # auto가 이미 같은 값이면 스킵
        if lab.status == status and lab.labeled_by != "user":
            continue

        lab.status = status
        lab.confidence = 100
        lab.labeled_by = "auto"  # 규칙으로 자동 적용
        lab.decided_at = utcnow()
        db.session.add(lab)
        updated += 1

    return updated


def _apply_expense_rule_this_month(
    *,
    user_pk: int,
    counterparty_key: str,
    status: str,  # business/personal
    start_dt: datetime,
    end_dt: datetime,
    exclude_tx_id: int,
) -> tuple[int, int]:
    tx_ids = [
        r[0]
        for r in (
            db.session.query(Transaction.id)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.counterparty == counterparty_key)
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(Transaction.id != exclude_tx_id)
            .all()
        )
    ]
    if not tx_ids:
        return 0, 0

    labels = (
        db.session.query(ExpenseLabel)
        .filter(ExpenseLabel.user_pk == user_pk)
        .filter(ExpenseLabel.transaction_id.in_(tx_ids))
        .all()
    )
    label_map = {l.transaction_id: l for l in labels}

    evidences = (
        db.session.query(EvidenceItem)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(EvidenceItem.transaction_id.in_(tx_ids))
        .all()
    )
    ev_map = {e.transaction_id: e for e in evidences}

    req, ev_status_default = _evidence_defaults_from_expense_status(status)

    updated_labels = 0
    updated_evidence = 0

    for tx_id in tx_ids:
        lab = label_map.get(tx_id)
        if lab and lab.labeled_by == "user":
            continue  # 사용자가 이미 확정한 건은 존중

        if not lab:
            lab = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)

        if lab.status == status and lab.labeled_by != "user":
            pass
        else:
            lab.status = status
            lab.confidence = 100
            lab.labeled_by = "auto"
            lab.decided_at = utcnow()
            db.session.add(lab)
            updated_labels += 1

        # evidence도 같이 정리(첨부된 파일/attached는 건드리지 않음)
        ev = ev_map.get(tx_id)
        if not ev:
            ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement=req, status=ev_status_default)
            db.session.add(ev)
            updated_evidence += 1
        else:
            has_file = bool(ev.file_key) and (ev.deleted_at is None)
            if ev.status == "attached" or has_file:
                continue

            # personal이면 not_needed로, business면 required+missing으로
            new_req, new_st = req, ev_status_default

            if ev.requirement != new_req or ev.status != new_st:
                ev.requirement = new_req
                ev.status = new_st
                db.session.add(ev)
                updated_evidence += 1

    return updated_labels, updated_evidence


def _month_key_from_tx(tx: Transaction) -> str:
    return tx.occurred_at.strftime("%Y-%m")


register_review_routes(
    bp=web_calendar_bp,
    uid_getter=_uid,
    parse_month=_parse_month,
    month_range=_month_range,
    month_key_from_tx=_month_key_from_tx,
    is_partial=_is_partial,
    utcnow_fn=utcnow,
    db=db,
    compute_tax_estimate=compute_tax_estimate,
    Transaction=Transaction,
    IncomeLabel=IncomeLabel,
    ExpenseLabel=ExpenseLabel,
    EvidenceItem=EvidenceItem,
    ReceiptExpenseFollowupAnswer=ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement=ReceiptExpenseReinforcement,
    CounterpartyRule=CounterpartyRule,
    CounterpartyExpenseRule=CounterpartyExpenseRule,
    ActionLog=ActionLog,
    default_retention_until=default_retention_until,
    delete_physical_file=delete_physical_file,
    resolve_file_path=resolve_file_path,
    store_evidence_file_multi=store_evidence_file_multi,
    store_evidence_text_file=store_evidence_text_file,
    parse_receipt_from_file=parse_receipt_from_file,
    parse_receipt_from_text=parse_receipt_from_text,
)


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

        label = db.session.query(IncomeLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
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

        label = db.session.query(ExpenseLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
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
    account_mode, account_scope_id, account_scope_value = _parse_account_scope(user_pk, request.args.get("account"))
    account_options = list_accounts_for_ui(
        user_pk,
        keep_ids=([account_scope_id] if account_mode == "account" and account_scope_id else None),
    )
    account_theme = _resolve_account_theme(
        account_options=account_options,
        scope_mode=account_mode,
        scope_account_id=account_scope_id,
        scope_value=account_scope_value,
    )
    selected_account = None
    if account_mode == "account" and account_scope_id:
        selected_account = next(
            (x for x in account_options if int(x.get("id") or 0) == int(account_scope_id)),
            None,
        )
    start_d, end_d = _month_range(month_first)

    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    today = date.today()

    rows = (
        _apply_account_scope(
            db.session.query(
            DAY_EXPR.label("d"),
            func.coalesce(func.sum(case((Transaction.direction == "in", Transaction.amount_krw), else_=0)), 0).label("income"),
            func.coalesce(func.sum(case((Transaction.direction == "out", Transaction.amount_krw), else_=0)), 0).label("expense"),
            func.count(Transaction.id).label("cnt"),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
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
        _apply_account_scope(
            db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(5)
        .all()
    )

    top_in = (
        _apply_account_scope(
            db.session.query(
            func.coalesce(Transaction.counterparty, "기타").label("name"),
            func.sum(Transaction.amount_krw).label("sum_amt"),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "in")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
        .group_by("name")
        .order_by(func.sum(Transaction.amount_krw).desc())
        .limit(5)
        .all()
    )

    prev_month = (month_first - timedelta(days=1)).replace(day=1)
    next_month = end_d
    grid = _calendar_grid(month_first)

    # ✅ 영수증 처리(Review) 기준과 동일한 TODO 카운트
    income_need = (
        _apply_account_scope(
            db.session.query(func.count(func.distinct(Transaction.id)))
            .select_from(Transaction)
            .outerjoin(
                IncomeLabel,
                and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "in")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
            .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
            .scalar()
    ) or 0

    receipt_required_missing = (
        _apply_account_scope(
            db.session.query(func.count(func.distinct(Transaction.id)))
            .select_from(Transaction)
            .join(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
            .filter(EvidenceItem.requirement == "required")
            .filter(EvidenceItem.status == "missing")
            .scalar()
    ) or 0

    receipt_attach_missing = (
        _apply_account_scope(
            db.session.query(func.count(func.distinct(Transaction.id)))
            .select_from(Transaction)
            .join(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
            )
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
            .filter(EvidenceItem.requirement == "maybe")
            .filter(EvidenceItem.status == "missing")
            .scalar()
    ) or 0

    expense_confirm_need = (
        _apply_account_scope(
            db.session.query(func.count(func.distinct(Transaction.id)))
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
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
            account_mode,
            account_scope_id,
        )
            .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status.in_(("unknown", "mixed"))))
            .filter(or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)))
            .scalar()
    ) or 0

    review_counts = {
        "receipt_required": int(receipt_required_missing or 0),
        "receipt_attach": int(receipt_attach_missing or 0),
        "expense_confirm": int(expense_confirm_need or 0),
        "income_confirm": int(income_need or 0),
    }
    onboarding_goal = get_primary_goal(user_pk)
    todo_recommended_focus = pick_focus_from_counts(review_counts, onboarding_goal)

    active_links = (
        db.session.query(func.count(BankAccountLink.id))
        .filter(BankAccountLink.user_pk == user_pk)
        .filter(BankAccountLink.is_active.is_(True))
        .scalar()
    ) or 0
    linked_accounts, linked_accounts_has_unavailable = get_linked_account_balances(user_pk, limit=6)

    urls = {
        "bank": _safe_url("web_bank.index") or "/bank",
        "inbox": _safe_url("web_inbox.index"),
    }

    month_key = month_first.strftime("%Y-%m")
    planned_by_day = build_planned_by_day(user_pk, month_first)
    
    
    # ✅ 캘린더 KPI: 세금 추정치 + 세금 제외 남은 돈(현금흐름 기준)
    tax_est = compute_tax_estimate(
        user_pk,
        month_key=month_key,
        prefer_monthly_signal=True,
    )
    tax_estimate = int(getattr(tax_est, "buffer_target_krw", 0) or 0)
    tax_balance = int(getattr(tax_est, "buffer_total_krw", 0) or 0)
    tax_shortage = max(0, tax_estimate - tax_balance)
    month_after_tax = int(month_net) - int(tax_estimate)
    
    # ✅ (추가) 건보료(예정) 자동 감지 + 안전하게 쓸 수 있는 돈
    nhi = infer_nhi_for_month(user_pk, month_key)
    nhi_monthly = int(nhi.monthly_krw or 0)
    nhi_due = int(nhi.due_krw or 0)  # 이번달에 이미 납부했으면 0

    # ✅ 보수 안전금액: 남은 돈에서 "예상세액 전체"를 뺀다
    public_due = int(tax_estimate) + int(nhi_due)
    safe_to_spend = int(month_net) - int(public_due)

    # 참고용: 추가로 더 떼어둘 돈(금고/예정 기반)
    additional_to_reserve = int(tax_shortage) + int(nhi_due)

    receipt_effect_nav_params = _receipt_effect_nav_params()
    tax_buffer_url = url_for(
        "web_calendar.tax_buffer",
        month=month_key,
        account=account_scope_value,
        **receipt_effect_nav_params,
    )

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
        tax_estimate=tax_estimate,
        month_after_tax=month_after_tax,
        tax_balance=tax_balance,
        tax_shortage=tax_shortage,
        tax_rate=float(getattr(tax_est, "tax_rate", 0) or 0),
        included_income=int(getattr(tax_est, "income_included_krw", 0) or 0),
        business_expense=int(getattr(tax_est, "expense_business_krw", 0) or 0),
        receipt_reflected_expense_krw=int(getattr(tax_est, "receipt_reflected_expense_krw", 0) or 0),
        receipt_pending_expense_krw=int(getattr(tax_est, "receipt_pending_expense_krw", 0) or 0),
        est_profit=int(getattr(tax_est, "estimated_profit_krw", 0) or 0),
        top_out=top_out,
        top_in=top_in,
        income_need=int(income_need),
        receipt_required_missing=int(receipt_required_missing),
        receipt_attach_missing=int(receipt_attach_missing),
        expense_confirm_need=int(expense_confirm_need),
        onboarding_goal=(onboarding_goal or ""),
        todo_recommended_focus=todo_recommended_focus,
        active_links=int(active_links),
        linked_accounts=linked_accounts,
        linked_accounts_has_unavailable=bool(linked_accounts_has_unavailable),
        urls=urls,
        month_key=month_key,
        planned_by_day=planned_by_day,
        snapshot=snapshot,
        nhi_monthly=nhi_monthly,
        nhi_due=nhi_due,
        nhi_paid_this_month=bool(getattr(nhi, "paid_this_month", False)),
        nhi_source=str(getattr(nhi, "source", "unknown")),
        public_due=public_due,
        safe_to_spend=safe_to_spend,
        additional_to_reserve=additional_to_reserve,
        receipt_effect_event=(request.args.get("receipt_effect_event") == "1"),
        receipt_effect_level=str(request.args.get("receipt_effect_level") or ""),
        current_tax_due_est_krw=_receipt_effect_int_arg("current_tax_due_est_krw"),
        current_buffer_target_krw=_receipt_effect_int_arg("current_buffer_target_krw"),
        tax_delta_from_receipts_krw=_receipt_effect_int_arg("tax_delta_from_receipts_krw"),
        buffer_delta_from_receipts_krw=_receipt_effect_int_arg("buffer_delta_from_receipts_krw"),
        tax_before=_receipt_effect_int_arg("tax_before"),
        tax_after=_receipt_effect_int_arg("tax_after"),
        buffer_before=_receipt_effect_int_arg("buffer_before"),
        buffer_after=_receipt_effect_int_arg("buffer_after"),
        account_scope=account_theme,
        account_scope_value=account_scope_value,
        account_options=account_options,
        selected_account=selected_account,
        tax_buffer_url=tax_buffer_url,
    )


@web_calendar_bp.get("/day/<ymd>")
def day_detail(ymd: str):
    user_pk = _uid()
    d = datetime.strptime(ymd, "%Y-%m-%d").date()
    account_mode, account_scope_id, account_scope_value = _parse_account_scope(user_pk, request.args.get("account"))
    account_options = list_accounts_for_ui(
        user_pk,
        keep_ids=([account_scope_id] if account_mode == "account" and account_scope_id else None),
    )
    account_theme = _resolve_account_theme(
        account_options=account_options,
        scope_mode=account_mode,
        scope_account_id=account_scope_id,
        scope_value=account_scope_value,
    )
    selected_account = None
    if account_mode == "account" and account_scope_id:
        selected_account = next(
            (x for x in account_options if int(x.get("id") or 0) == int(account_scope_id)),
            None,
        )

    start_dt = datetime.combine(d, time.min)
    end_dt = start_dt + timedelta(days=1)

    direction = (request.args.get("dir") or "all").strip()
    if direction not in ("all", "in", "out"):
        direction = "all"

    q = (request.args.get("q") or "").strip()

    query = _apply_account_scope(
        (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        ),
        account_mode,
        account_scope_id,
    )

    if direction in ("in", "out"):
        query = query.filter(Transaction.direction == direction)

    if q:
        like = f"%{q}%"
        query = query.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    txs = query.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).all()

    # account=all에서도 거래별 계좌를 보여주기 위한 배지 데이터(숨김 계좌 포함)
    account_badge_options = list_accounts_for_ui(user_pk, include_hidden=True)
    account_badge_map: dict[int, dict[str, str]] = {}
    for item in account_badge_options:
        try:
            account_id = int(item.get("id") or 0)
        except Exception:
            account_id = 0
        if account_id <= 0:
            continue
        account_badge_map[account_id] = {
            "name": str(item.get("display_name") or "미지정"),
            "color_hex": str(item.get("color_hex") or "#64748B"),
        }

    for tx in txs:
        try:
            tx_account_id = int(getattr(tx, "bank_account_id", 0) or 0)
        except Exception:
            tx_account_id = 0
        badge = account_badge_map.get(tx_account_id)
        if badge:
            tx.account_badge_name = str(badge.get("name") or "미지정")
            tx.account_badge_color = str(badge.get("color_hex") or "#64748B")
        else:
            tx.account_badge_name = "미지정"
            tx.account_badge_color = "#64748B"

    day_income = sum(t.amount_krw for t in txs if t.direction == "in")
    day_expense = sum(t.amount_krw for t in txs if t.direction == "out")
    day_net = day_income - day_expense

    def _top(direction_value: str):
        q2 = (
            _apply_account_scope(
                db.session.query(
                func.coalesce(Transaction.counterparty, "기타").label("name"),
                func.sum(Transaction.amount_krw).label("sum_amt"),
                func.count(Transaction.id).label("cnt"),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == direction_value)
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt),
                account_mode,
                account_scope_id,
            )
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
        account_scope=account_theme,
        account_scope_value=account_scope_value,
        account_options=account_options,
        selected_account=selected_account,
    )


@web_calendar_bp.post("/calendar/account-color")
def calendar_account_color():
    user_pk = _uid()
    account_scope_value = (request.form.get("account") or "").strip()
    month_value = (request.form.get("month") or "").strip()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.month_calendar", month=(month_value or None), account=(account_scope_value or None)),
    )
    account_id = _parse_post_account_id(user_pk, request.form.get("account_id"), allow_blank=False)
    color_raw = (request.form.get("color_hex") or "").strip()
    has_color_field = "color_hex" in request.form
    has_alias_field = "alias" in request.form
    alias_value = safe_str(request.form.get("alias"), max_len=64) if has_alias_field else None
    if not account_id:
        flash("계좌를 다시 선택해 주세요.", "error")
        return redirect(next_url)
    if not has_color_field and not has_alias_field:
        flash("수정할 항목을 찾지 못했어요.", "error")
        return redirect(next_url)
    if has_color_field and not color_raw:
        flash("색상을 선택해 주세요.", "error")
        return redirect(next_url)
    if has_color_field:
        if not color_raw.startswith("#"):
            color_raw = f"#{color_raw}"
        if not _HEX_COLOR_RE.fullmatch(color_raw):
            flash("색상 형식이 올바르지 않아요.", "error")
            return redirect(next_url)

    try:
        row = (
            UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.id == int(account_id))
            .first()
        )
    except Exception:
        db.session.rollback()
        flash("계좌 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(next_url)
    if not row:
        flash("계좌를 찾을 수 없어요.", "error")
        return redirect(next_url)
    try:
        if has_alias_field:
            row.alias = alias_value or None
        if has_color_field:
            row.color_hex = color_raw.upper()
        db.session.add(row)
        db.session.commit()
        if has_alias_field and has_color_field:
            flash("계좌 이름과 색상을 저장했어요.", "success")
        elif has_alias_field:
            flash("계좌 이름을 저장했어요.", "success")
        else:
            flash("계좌 색상을 저장했어요.", "success")
    except Exception:
        db.session.rollback()
        flash("계좌 저장에 실패했어요. 잠시 후 다시 시도해 주세요.", "error")
    return redirect(next_url)


@web_calendar_bp.post("/day/<ymd>/quick-add")
def day_quick_add(ymd: str):
    user_pk = _uid()
    account_scope_value = (request.form.get("account") or "").strip()

    try:
        d = datetime.strptime(ymd, "%Y-%m-%d").date()
    except ValueError:
        flash("날짜 형식이 올바르지 않습니다.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd, account=(account_scope_value or None)))

    direction = safe_str(request.form.get("direction"), max_len=8)
    amount_krw = parse_int_krw(request.form.get("amount_krw")) or 0
    counterparty = safe_str(request.form.get("counterparty"), max_len=120) or None
    memo = safe_str(request.form.get("memo"), max_len=255) or None
    bank_account_id = _parse_post_account_id(user_pk, request.form.get("bank_account_id"), allow_blank=True)
    if not bank_account_id:
        bank_account_id = int(ensure_manual_bucket(user_pk).id)

    if direction not in ("in", "out"):
        flash("구분을 선택해주세요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd, account=(account_scope_value or None)))

    if not amount_krw or amount_krw <= 0:
        flash("금액은 1원 이상 입력해주세요.", "error")
        return redirect(url_for("web_calendar.day_detail", ymd=ymd, account=(account_scope_value or None)))

    tx = Transaction(
        user_pk=user_pk,
        import_job_id=None,
        occurred_at=datetime.combine(d, time.min),
        direction=direction,
        amount_krw=amount_krw,
        counterparty=counterparty,
        memo=memo,
        source="manual",
        bank_account_id=int(bank_account_id) if bank_account_id else None,
        external_hash=uuid4().hex,
    )

    try:
        db.session.add(tx)
        db.session.commit()
        flash("거래가 추가되었습니다.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("저장 중 문제가 발생했어요. 다시 시도해주세요.", "error")

    return redirect(url_for("web_calendar.day_detail", ymd=ymd, account=(account_scope_value or None)))


@web_calendar_bp.post("/day/<ymd>/tx/<int:tx_id>/update")
def day_tx_update(ymd: str, tx_id: int):
    user_pk = _uid()
    account_scope_value = (request.form.get("account") or "").strip()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.day_detail", ymd=ymd, account=(account_scope_value or None)),
    )
    try:
        d = datetime.strptime(ymd, "%Y-%m-%d").date()
    except ValueError:
        flash("날짜 형식이 올바르지 않습니다.", "error")
        return redirect(next_url)

    tx = (
        db.session.query(Transaction)
        .filter_by(user_pk=user_pk, id=int(tx_id))
        .first()
    )
    if not tx:
        flash("수정할 거래를 찾을 수 없어요.", "error")
        return redirect(next_url)

    direction = safe_str(request.form.get("direction"), max_len=8)
    if direction not in ("in", "out"):
        flash("구분을 올바르게 선택해 주세요.", "error")
        return redirect(next_url)

    amount_krw = parse_int_krw(request.form.get("amount_krw")) or 0
    if amount_krw <= 0:
        flash("금액은 1원 이상 입력해 주세요.", "error")
        return redirect(next_url)

    time_raw = safe_str(request.form.get("time") or "00:00", max_len=8) or "00:00"
    try:
        hh, mm = time_raw.split(":", 1)
        tx_time = time(int(hh), int(mm))
    except Exception:
        flash("시간 형식이 올바르지 않습니다.", "error")
        return redirect(next_url)

    counterparty = safe_str(request.form.get("counterparty"), max_len=120) or None
    memo = safe_str(request.form.get("memo"), max_len=255) or None
    bank_account_raw = (request.form.get("bank_account_id") or "").strip()
    bank_account_id = _parse_post_account_id(user_pk, bank_account_raw, allow_blank=True)
    if bank_account_raw and not bank_account_id:
        flash("계좌 선택값을 확인해 주세요.", "error")
        return redirect(next_url)

    try:
        tx.direction = direction
        tx.amount_krw = int(amount_krw)
        tx.occurred_at = datetime.combine(d, tx_time)
        tx.counterparty = counterparty
        tx.memo = memo
        tx.bank_account_id = int(bank_account_id) if bank_account_id else None
        db.session.add(tx)
        db.session.commit()
        flash("거래 내용을 수정했어요.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("수정 저장 중 문제가 발생했어요. 다시 시도해 주세요.", "error")
    except Exception:
        db.session.rollback()
        flash("수정 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")

    return redirect(next_url)


@web_calendar_bp.post("/day/<ymd>/tx/<int:tx_id>/delete")
def day_tx_delete(ymd: str, tx_id: int):
    user_pk = _uid()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.day_detail", ymd=ymd),
    )
    tx = (
        db.session.query(Transaction)
        .filter_by(user_pk=user_pk, id=int(tx_id))
        .first()
    )
    if not tx:
        flash("삭제할 거래를 찾을 수 없어요.", "error")
        return redirect(next_url)

    file_key_to_delete = None
    try:
        inc = db.session.query(IncomeLabel).filter_by(user_pk=user_pk, transaction_id=tx.id).first()
        if inc:
            db.session.delete(inc)

        exp = db.session.query(ExpenseLabel).filter_by(user_pk=user_pk, transaction_id=tx.id).first()
        if exp:
            db.session.delete(exp)

        ev = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx.id).first()
        if ev:
            if ev.file_key and ev.deleted_at is None:
                file_key_to_delete = str(ev.file_key)
            db.session.delete(ev)

        db.session.delete(tx)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("거래 삭제 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")
        return redirect(next_url)

    if file_key_to_delete:
        try:
            delete_physical_file(file_key_to_delete)
        except Exception:
            # 파일 삭제 실패는 사용자 동선을 막지 않는다.
            pass

    flash("거래를 삭제했어요.", "success")
    return redirect(next_url)


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


# ---- 아래 3개는 기존 호환용(남겨둠) ----
@web_calendar_bp.post("/label/income")
def set_income_label():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "unknown").strip()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.month_calendar"),
    )

    if status not in ("income", "non_income", "unknown"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    label = db.session.query(IncomeLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not label:
        label = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = utcnow()

    db.session.add(label)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/label/expense")
def set_expense_label():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "unknown").strip()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.month_calendar"),
    )

    if status not in ("business", "personal", "mixed", "unknown"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    label = db.session.query(ExpenseLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not label:
        label = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)

    label.status = status
    label.confidence = 100
    label.labeled_by = "user"
    label.decided_at = utcnow()

    db.session.add(label)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/evidence")
def set_evidence_status():
    user_pk = _uid()
    tx_id = int(request.form.get("tx_id") or 0)
    status = (request.form.get("status") or "missing").strip()
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.month_calendar"),
    )

    if status not in ("missing", "attached", "not_needed"):
        return redirect(next_url)

    tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
    if not tx:
        return redirect(next_url)

    item = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
    if not item:
        item = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement="maybe", status="missing")

    item.status = status
    item.updated_at = utcnow()

    db.session.add(item)
    db.session.commit()
    return redirect(next_url)


@web_calendar_bp.post("/month-close")
def month_close():
    user_pk = _uid()
    month_first = _parse_month(request.form.get("month"))
    month_key = month_first.strftime("%Y-%m")
    account_scope_value = (request.form.get("account") or "").strip()
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

    return redirect(url_for("web_calendar.month_calendar", month=month_key, account=(account_scope_value or None)))


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
    account_mode, account_scope_id, account_scope_value = _parse_account_scope(user_pk, request.args.get("account"))
    account_options = list_accounts_for_ui(
        user_pk,
        keep_ids=([account_scope_id] if account_mode == "account" and account_scope_id else None),
    )
    manual_bucket = ensure_manual_bucket(user_pk)
    default_bank_account_id = int(manual_bucket.id)
    if account_mode == "account" and account_scope_id:
        default_bank_account_id = int(account_scope_id)

    recent = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(8)
        .all()
    )

    next_url = sanitize_next_url(
        request.args.get("next"),
        fallback=(
            url_for("web_calendar.month_calendar", month=month_key, account=(account_scope_value or None))
            if month_key
            else url_for("web_calendar.month_calendar", account=(account_scope_value or None))
        ),
    )

    return render_template(
        "calendar/tx_new.html",
        month_key=month_key,
        default_date=default_date,
        recent=recent,
        next_url=next_url,
        account_options=account_options,
        account_scope_value=account_scope_value,
        default_bank_account_id=default_bank_account_id,
    )


@web_calendar_bp.post("/tx/new")
def tx_create():
    user_pk = _uid()

    ymd = safe_str(request.form.get("date"), max_len=10)  # YYYY-MM-DD
    hhmm = safe_str(request.form.get("time") or "12:00", max_len=8) or "12:00"
    direction = safe_str(request.form.get("direction") or "out", max_len=8)
    amount = parse_int_krw(request.form.get("amount_krw")) or 0
    counterparty = safe_str(request.form.get("counterparty"), max_len=120) or None
    memo = safe_str(request.form.get("memo"), max_len=255) or None
    bank_account_id = _parse_post_account_id(user_pk, request.form.get("bank_account_id"), allow_blank=True)
    if not bank_account_id:
        bank_account_id = int(ensure_manual_bucket(user_pk).id)
    next_url = sanitize_next_url(
        request.form.get("next"),
        fallback=url_for("web_calendar.month_calendar"),
    )

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
        bank_account_id=int(bank_account_id) if bank_account_id else None,
        external_hash=external_hash,
    )
    db.session.add(tx)
    db.session.commit()

    return redirect(next_url)


@web_calendar_bp.get("/search")
def month_search():
    user_pk = _uid()
    month_first = _parse_month(request.args.get("month"))
    account_mode, account_scope_id, account_scope_value = _parse_account_scope(user_pk, request.args.get("account"))
    q = safe_str(request.args.get("q"), max_len=120)

    start_d, end_d = _month_range(month_first)
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.min)

    query = _apply_account_scope(
        (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        ),
        account_mode,
        account_scope_id,
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
        account_scope_value=account_scope_value,
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
    direction = safe_str(request.form.get("direction") or "out", max_len=8)
    amount = parse_int_krw(request.form.get("amount_krw")) or 0
    cadence = safe_str(request.form.get("cadence") or "monthly", max_len=16)
    day_of_month = parse_int_krw(request.form.get("day_of_month"))
    weekday = parse_int_krw(request.form.get("weekday"))
    counterparty = safe_str(request.form.get("counterparty"), max_len=120) or None
    memo = safe_str(request.form.get("memo"), max_len=255) or None

    rr = RecurringRule(
        user_pk=user_pk,
        direction=direction if direction in ("in", "out") else "out",
        amount_krw=clamp_int(amount, minimum=0, maximum=1_000_000_000_000, default=0),
        cadence=cadence if cadence in ("monthly", "weekly") else "monthly",
        day_of_month=clamp_int(day_of_month, minimum=1, maximum=31, default=1) if day_of_month else None,
        weekday=clamp_int(weekday, minimum=0, maximum=6, default=0) if weekday is not None else None,
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
    rid = clamp_int(parse_int_krw(request.form.get("id")) or 0, minimum=0, maximum=2_147_483_647)
    rr = db.session.query(RecurringRule).filter_by(id=rid, user_pk=user_pk).first()
    if rr:
        rr.is_active = not rr.is_active
        db.session.commit()
    return redirect(url_for("web_calendar.recurring_list"))
