from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func

from core.time import utcnow
from core.extensions import db
from domain.models import ExpenseLabel, IncomeLabel, Transaction
from services.onboarding import get_tax_profile


def _as_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("원", "").strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
    except Exception:
        return None
    return int(max(0, parsed))


def build_tax_input_draft(
    user_pk: int,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """세금 기본 입력값 초안을 생성한다.

    초안은 저장 전 상태이며 high/exact 승급 근거로 사용하면 안 된다.
    """
    p = dict(profile or get_tax_profile(int(user_pk)) or {})
    draft_values: dict[str, int | None] = {}
    draft_source: dict[str, str] = {}
    draft_confidence: dict[str, str] = {}

    now = utcnow()
    start_dt = now - timedelta(days=365)

    if _as_int_or_none(p.get("annual_gross_income_krw")) is None:
        income_sum = (
            db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
            .select_from(Transaction)
            .outerjoin(
                IncomeLabel,
                (IncomeLabel.transaction_id == Transaction.id) & (IncomeLabel.user_pk == int(user_pk)),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "in")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at <= now)
            .filter((IncomeLabel.status.is_(None)) | (IncomeLabel.status != "non_income"))
            .scalar()
        ) or 0
        if int(income_sum) > 0:
            draft_values["annual_gross_income_krw"] = int(income_sum)
            draft_source["annual_gross_income_krw"] = "transactions_12m_income"
            draft_confidence["annual_gross_income_krw"] = "medium"

    if _as_int_or_none(p.get("annual_deductible_expense_krw")) is None:
        expense_sum = (
            db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
            .select_from(Transaction)
            .outerjoin(
                ExpenseLabel,
                (ExpenseLabel.transaction_id == Transaction.id) & (ExpenseLabel.user_pk == int(user_pk)),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at <= now)
            .filter(ExpenseLabel.status == "business")
            .scalar()
        ) or 0
        if int(expense_sum) >= 0:
            draft_values["annual_deductible_expense_krw"] = int(expense_sum)
            draft_source["annual_deductible_expense_krw"] = "transactions_12m_business_expense"
            draft_confidence["annual_deductible_expense_krw"] = "medium"

    if _as_int_or_none(p.get("withheld_tax_annual_krw")) is None:
        income_rows = (
            db.session.query(Transaction.amount_krw, Transaction.counterparty, Transaction.memo)
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "in")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at <= now)
            .all()
        )
        heuristic_base = 0
        for amount, counterparty, memo in income_rows:
            text = f"{counterparty or ''} {memo or ''}".lower()
            if ("3.3" in text) or ("원천" in text):
                heuristic_base += int(amount or 0)
        if heuristic_base > 0:
            draft_values["withheld_tax_annual_krw"] = int(round(int(heuristic_base) * 0.033))
            draft_source["withheld_tax_annual_krw"] = "heuristic_withholding_33"
            draft_confidence["withheld_tax_annual_krw"] = "low"

    if _as_int_or_none(p.get("prepaid_tax_annual_krw")) is None:
        draft_values["prepaid_tax_annual_krw"] = 0
        draft_source["prepaid_tax_annual_krw"] = "default_zero_suggestion"
        draft_confidence["prepaid_tax_annual_krw"] = "low"

    if str(p.get("income_classification") or "unknown").strip().lower() == "unknown":
        draft_source["income_classification"] = "user_input_required"
        draft_confidence["income_classification"] = "none"

    return {
        "draft_values": draft_values,
        "draft_source": draft_source,
        "draft_confidence": draft_confidence,
        "has_draft": bool(draft_values),
        "requires_user_confirmation": True,
    }
