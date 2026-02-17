# services/receipts.py
"""services/receipts.py

증빙/분류 인박스(운영감의 핵심).

이 모듈은 UI에서 바로 쓰는 '큐'를 만든다:
- 증빙 누락(확실): requirement=required AND status=missing
- 증빙 확인 필요: requirement=maybe AND status=missing
- 개인/업무 섞임: expense_labels.status IN (unknown,mixed)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.extensions import db
from core.time import utcnow
from domain.models import (
    Transaction,
    IncomeLabel,
    CounterpartyRule,
    ExpenseLabel,
    CounterpartyExpenseRule,
    EvidenceItem,
)

def _normalize_key(v: Optional[str]) -> str:
    if not v:
        return ""
    s = str(v).strip()
    s = " ".join(s.split())
    return s.lower()

@dataclass
class InboxTx:
    id: int
    occurred_at: str
    direction: str
    amount_krw: int
    counterparty: str
    memo: str
    income_status: str
    expense_status: str
    evidence_requirement: str
    evidence_status: str

def _fmt_dt(tx: Transaction) -> str:
    try:
        from zoneinfo import ZoneInfo
        kst = ZoneInfo("Asia/Seoul")
        return tx.occurred_at.astimezone(kst).strftime("%Y-%m-%d")
    except Exception:
        return tx.occurred_at.strftime("%Y-%m-%d")

def _to_inbox_tx(tx: Transaction, il: Optional[IncomeLabel], el: Optional[ExpenseLabel], ev: Optional[EvidenceItem]) -> InboxTx:
    return InboxTx(
        id=tx.id,
        occurred_at=_fmt_dt(tx),
        direction=tx.direction,
        amount_krw=int(tx.amount_krw),
        counterparty=(tx.counterparty or ""),
        memo=(tx.memo or ""),
        income_status=(il.status if il else ""),
        expense_status=(el.status if el else ""),
        evidence_requirement=(ev.requirement if ev else ""),
        evidence_status=(ev.status if ev else ""),
    )

def get_inbox_sections(user_pk: int, limit_each: int = 30) -> Dict[str, List[InboxTx]]:
    required_q = (
        db.session.query(Transaction, EvidenceItem)
        .join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(
            Transaction.user_pk == user_pk,
            Transaction.direction == "out",
            EvidenceItem.requirement == "required",
            EvidenceItem.status == "missing",
        )
        .order_by(Transaction.occurred_at.desc())
        .limit(limit_each)
        .all()
    )

    maybe_q = (
        db.session.query(Transaction, EvidenceItem)
        .join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(
            Transaction.user_pk == user_pk,
            Transaction.direction == "out",
            EvidenceItem.requirement == "maybe",
            EvidenceItem.status == "missing",
        )
        .order_by(Transaction.occurred_at.desc())
        .limit(limit_each)
        .all()
    )

    exp_q = (
        db.session.query(Transaction, ExpenseLabel)
        .join(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .filter(
            Transaction.user_pk == user_pk,
            Transaction.direction == "out",
            ExpenseLabel.status.in_(["unknown", "mixed"]),
        )
        .order_by(Transaction.occurred_at.desc())
        .limit(limit_each)
        .all()
    )

    recent = (
        db.session.query(Transaction, IncomeLabel, ExpenseLabel, EvidenceItem)
        .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
        .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
        .outerjoin(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
        .filter(Transaction.user_pk == user_pk)
        .order_by(Transaction.occurred_at.desc())
        .limit(limit_each)
        .all()
    )

    required_items: List[InboxTx] = []
    for tx, ev in required_q:
        el = ExpenseLabel.query.filter_by(transaction_id=tx.id).first()
        required_items.append(_to_inbox_tx(tx, None, el, ev))

    maybe_items: List[InboxTx] = []
    for tx, ev in maybe_q:
        el = ExpenseLabel.query.filter_by(transaction_id=tx.id).first()
        maybe_items.append(_to_inbox_tx(tx, None, el, ev))

    expense_items: List[InboxTx] = []
    for tx, el in exp_q:
        ev = EvidenceItem.query.filter_by(transaction_id=tx.id).first()
        expense_items.append(_to_inbox_tx(tx, None, el, ev))

    recent_items: List[InboxTx] = []
    for tx, il, el, ev in recent:
        recent_items.append(_to_inbox_tx(tx, il, el, ev))

    return {"required": required_items, "maybe": maybe_items, "expense": expense_items, "recent": recent_items}

def set_income_label(user_pk: int, transaction_id: int, status: str, save_rule: bool = False) -> None:
    if status not in ("income", "non_income", "unknown"):
        raise ValueError("invalid income status")

    tx = Transaction.query.filter_by(id=transaction_id, user_pk=user_pk).first_or_404()
    if tx.direction != "in":
        raise ValueError("not an income transaction")

    il = IncomeLabel.query.filter_by(transaction_id=transaction_id).first()
    if not il:
        il = IncomeLabel(user_pk=user_pk, transaction_id=transaction_id)
        db.session.add(il)

    il.status = status
    il.labeled_by = "user"
    il.confidence = 100 if status != "unknown" else 0
    il.decided_at = utcnow() if status != "unknown" else None
    db.session.commit()

    if save_rule and tx.counterparty:
        key = _normalize_key(tx.counterparty)
        _upsert_income_rule(user_pk=user_pk, counterparty_key=key, rule=status)

def _upsert_income_rule(user_pk: int, counterparty_key: str, rule: str) -> None:
    if rule not in ("income", "non_income"):
        return
    r = CounterpartyRule.query.filter_by(user_pk=user_pk, counterparty_key=counterparty_key).first()
    if not r:
        r = CounterpartyRule(user_pk=user_pk, counterparty_key=counterparty_key, rule=rule, active=True)
        db.session.add(r)
    else:
        r.rule = rule
        r.active = True
    db.session.commit()

def set_expense_label(user_pk: int, transaction_id: int, status: str, save_rule: bool = False) -> None:
    if status not in ("business", "personal", "mixed", "unknown"):
        raise ValueError("invalid expense status")

    tx = Transaction.query.filter_by(id=transaction_id, user_pk=user_pk).first_or_404()
    if tx.direction != "out":
        raise ValueError("not an expense transaction")

    el = ExpenseLabel.query.filter_by(transaction_id=transaction_id).first()
    if not el:
        el = ExpenseLabel(user_pk=user_pk, transaction_id=transaction_id)
        db.session.add(el)

    el.status = status
    el.labeled_by = "user"
    el.confidence = 100 if status != "unknown" else 0
    el.decided_at = utcnow() if status != "unknown" else None
    db.session.commit()

    _sync_evidence_from_expense_label(user_pk=user_pk, transaction_id=transaction_id)

    if save_rule and tx.counterparty and status in ("business", "personal"):
        key = _normalize_key(tx.counterparty)
        _upsert_expense_rule(user_pk=user_pk, counterparty_key=key, rule=status)

def _upsert_expense_rule(user_pk: int, counterparty_key: str, rule: str) -> None:
    if rule not in ("business", "personal"):
        return
    r = CounterpartyExpenseRule.query.filter_by(user_pk=user_pk, counterparty_key=counterparty_key).first()
    if not r:
        r = CounterpartyExpenseRule(user_pk=user_pk, counterparty_key=counterparty_key, rule=rule, active=True)
        db.session.add(r)
    else:
        r.rule = rule
        r.active = True
    db.session.commit()

def set_evidence_status(user_pk: int, transaction_id: int, status: str) -> None:
    if status not in ("missing", "attached", "not_needed"):
        raise ValueError("invalid evidence status")

    tx = Transaction.query.filter_by(id=transaction_id, user_pk=user_pk).first_or_404()
    if tx.direction != "out":
        raise ValueError("not an expense transaction")

    ev = EvidenceItem.query.filter_by(transaction_id=transaction_id).first()
    if not ev:
        ev = EvidenceItem(user_pk=user_pk, transaction_id=transaction_id)
        db.session.add(ev)

    ev.status = status
    if status == "not_needed":
        ev.requirement = "not_needed"
    db.session.commit()

def _sync_evidence_from_expense_label(user_pk: int, transaction_id: int) -> None:
    el = ExpenseLabel.query.filter_by(transaction_id=transaction_id).first()
    if not el:
        return

    ev = EvidenceItem.query.filter_by(transaction_id=transaction_id).first()
    if not ev:
        ev = EvidenceItem(user_pk=user_pk, transaction_id=transaction_id)
        db.session.add(ev)

    if el.status == "business":
        ev.requirement = "required"
        if ev.status == "not_needed":
            ev.status = "missing"
    elif el.status == "personal":
        ev.requirement = "not_needed"
        ev.status = "not_needed"
    else:
        ev.requirement = "maybe"
        if ev.status == "not_needed":
            ev.status = "missing"

    db.session.commit()
