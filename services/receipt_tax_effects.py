from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy import and_

from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    Transaction,
)
from services.receipt_expense_rules import (
    evaluate_receipt_expense_with_follow_up,
    load_receipt_follow_up_answers_map,
    load_receipt_reinforcement_map,
)


@dataclass(frozen=True)
class ReceiptTaxEffectEntry:
    transaction_id: int
    amount_krw: int
    level: str
    summary: str
    reason: str
    expense_status: str


@dataclass(frozen=True)
class ReceiptTaxEffectsSummary:
    reflected_expense_krw: int
    pending_review_expense_krw: int
    excluded_expense_krw: int
    consult_tax_review_expense_krw: int
    reflected_transaction_count: int
    pending_transaction_count: int
    excluded_transaction_count: int
    consult_tax_review_transaction_count: int
    skipped_manual_transaction_count: int
    evaluated_transaction_count: int
    entries: tuple[ReceiptTaxEffectEntry, ...]


def _month_range_kst_naive(month_key: str) -> tuple[datetime, datetime]:
    base = datetime.strptime(str(month_key or "").strip(), "%Y-%m")
    if base.month == 12:
        next_month = base.replace(year=base.year + 1, month=1)
    else:
        next_month = base.replace(month=base.month + 1)
    return base, next_month


def _parse_receipt_draft_from_evidence(ev: Any | None) -> tuple[dict[str, Any], str]:
    note = str(getattr(ev, "note", "") or "")
    draft: dict[str, Any] = {}
    receipt_type = ""
    if note.startswith("receipt_draft:"):
        try:
            draft = json.loads(note[len("receipt_draft:") :])
        except Exception:
            draft = {}
    elif note.startswith("receipt_parse:"):
        try:
            draft = json.loads(note[len("receipt_parse:") :])
        except Exception:
            draft = {}
    if isinstance(draft, dict):
        receipt_type = str(draft.get("receipt_type") or "")
    if not receipt_type and note.startswith("receipt_meta:"):
        try:
            meta = json.loads(note[len("receipt_meta:") :])
            receipt_type = str(meta.get("receipt_type") or "")
        except Exception:
            receipt_type = ""
    if not receipt_type and str(getattr(ev, "file_key", "") or "").strip():
        receipt_type = "paper"
    return (draft if isinstance(draft, dict) else {}), receipt_type


def _has_receipt_context(
    ev: Any | None,
    *,
    follow_up_answers: dict[str, Any] | None = None,
    reinforcement_data: dict[str, Any] | None = None,
) -> bool:
    if ev is not None:
        if str(getattr(ev, "file_key", "") or "").strip():
            return True
        note = str(getattr(ev, "note", "") or "")
        if note.startswith(("receipt_draft:", "receipt_parse:", "receipt_meta:")):
            return True
    if follow_up_answers:
        return True
    if reinforcement_data:
        for key, value in dict(reinforcement_data or {}).items():
            if key in {"updated_at", "updated_by"}:
                continue
            if isinstance(value, bool) and value:
                return True
            if str(value or "").strip():
                return True
    return False


def summarize_receipt_tax_effect_entries(
    entries: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> ReceiptTaxEffectsSummary:
    seen_tx_ids: set[int] = set()
    reflected_expense = 0
    pending_expense = 0
    excluded_expense = 0
    consult_expense = 0
    reflected_count = 0
    pending_count = 0
    excluded_count = 0
    consult_count = 0
    skipped_manual = 0
    evaluated_count = 0
    normalized_entries: list[ReceiptTaxEffectEntry] = []

    for row in entries or ():
        tx_id = int((row or {}).get("transaction_id") or 0)
        if tx_id <= 0 or tx_id in seen_tx_ids:
            continue
        seen_tx_ids.add(tx_id)

        amount = int((row or {}).get("amount_krw") or 0)
        expense_status = str((row or {}).get("expense_status") or "unknown")
        if expense_status in {"business", "personal"}:
            skipped_manual += 1
            continue

        level = str((row or {}).get("level") or "").strip()
        if level not in {
            "high_likelihood",
            "needs_review",
            "do_not_auto_allow",
            "consult_tax_review",
        }:
            continue

        evaluated_count += 1
        normalized_entries.append(
            ReceiptTaxEffectEntry(
                transaction_id=tx_id,
                amount_krw=amount,
                level=level,
                summary=str((row or {}).get("summary") or ""),
                reason=str((row or {}).get("reason") or ""),
                expense_status=expense_status,
            )
        )
        if level == "high_likelihood":
            reflected_expense += amount
            reflected_count += 1
        elif level == "needs_review":
            pending_expense += amount
            pending_count += 1
        elif level == "do_not_auto_allow":
            excluded_expense += amount
            excluded_count += 1
        elif level == "consult_tax_review":
            consult_expense += amount
            consult_count += 1

    return ReceiptTaxEffectsSummary(
        reflected_expense_krw=int(reflected_expense),
        pending_review_expense_krw=int(pending_expense),
        excluded_expense_krw=int(excluded_expense),
        consult_tax_review_expense_krw=int(consult_expense),
        reflected_transaction_count=int(reflected_count),
        pending_transaction_count=int(pending_count),
        excluded_transaction_count=int(excluded_count),
        consult_tax_review_transaction_count=int(consult_count),
        skipped_manual_transaction_count=int(skipped_manual),
        evaluated_transaction_count=int(evaluated_count),
        entries=tuple(normalized_entries),
    )


def compute_receipt_tax_effects_for_month(
    db_session: Any,
    *,
    user_pk: int,
    month_key: str,
    transaction_rows: list[Any] | tuple[Any, ...] | None = None,
) -> ReceiptTaxEffectsSummary:
    start_dt, end_dt = _month_range_kst_naive(month_key)
    if transaction_rows is None:
        transaction_rows = (
            db_session.query(Transaction, ExpenseLabel, EvidenceItem)
            .select_from(Transaction)
            .outerjoin(
                ExpenseLabel,
                and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == int(user_pk)),
            )
            .outerjoin(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == int(user_pk)),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .all()
        )

    tx_ids = [
        int(getattr(tx, "id", 0) or 0)
        for tx, _, _ in (transaction_rows or ())
        if int(getattr(tx, "id", 0) or 0) > 0
    ]
    follow_up_map = load_receipt_follow_up_answers_map(
        db_session,
        ReceiptExpenseFollowupAnswer,
        user_pk=int(user_pk),
        transaction_ids=tx_ids,
    )
    reinforcement_map = load_receipt_reinforcement_map(
        db_session,
        ReceiptExpenseReinforcement,
        user_pk=int(user_pk),
        transaction_ids=tx_ids,
    )

    entries: list[dict[str, Any]] = []
    for tx, expense_label, ev in (transaction_rows or ()):
        tx_id = int(getattr(tx, "id", 0) or 0)
        if tx_id <= 0:
            continue
        expense_status = str(getattr(expense_label, "status", "") or "unknown")
        follow_up_answers = dict(follow_up_map.get(tx_id) or {})
        reinforcement_data = dict(reinforcement_map.get(tx_id) or {})
        if not _has_receipt_context(
            ev,
            follow_up_answers=follow_up_answers,
            reinforcement_data=reinforcement_data,
        ):
            continue

        draft, receipt_type = _parse_receipt_draft_from_evidence(ev)
        decision = evaluate_receipt_expense_with_follow_up(
            tx=tx,
            draft=draft,
            focus_kind=("expense_confirm" if expense_status == "mixed" else "receipt_attach"),
            receipt_type=receipt_type,
            follow_up_answers=follow_up_answers,
            reinforcement_data=reinforcement_data,
        )
        entries.append(
            {
                "transaction_id": tx_id,
                "amount_krw": int(getattr(tx, "amount_krw", 0) or 0),
                "level": str(decision.get("level") or ""),
                "summary": str(decision.get("summary") or ""),
                "reason": str(decision.get("why") or ""),
                "expense_status": expense_status,
            }
        )

    return summarize_receipt_tax_effect_entries(entries)
