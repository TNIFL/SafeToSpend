from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from domain.models import Transaction, UserBankAccount
from services.onboarding import (
    get_primary_goal,
    pick_focus_from_counts,
    tax_profile_completion_meta,
)
from services.bank_accounts import create_alias_account, get_linked_account_balances, list_accounts_for_ui
from services.analytics_events import record_seasonal_card_event
from services.input_sanitize import parse_bool_yn, parse_date_ym, parse_int_krw, safe_str
from services.nhis_runtime import compute_nhis_monthly_buffer
from services.receipt_expense_guidance import build_receipt_expense_inline_guidance
from services.receipt_expense_rules import (
    extract_follow_up_answers_from_form,
    extract_reinforcement_payload_from_form,
    load_receipt_follow_up_answers_map,
    load_receipt_reinforcement_map,
    save_receipt_follow_up_answers_and_re_evaluate,
    save_receipt_reinforcement_and_re_evaluate,
)
from services.risk import build_industry_missing_cost_hints, detect_large_transaction_outliers
from services.receipt_batch import normalize_receipt_error
from services.seasonal_ux import (
    activate_pending_seasonal_card,
    build_seasonal_experience,
    build_seasonal_screen_context,
    build_seasonal_tracking_query_params,
    clear_active_seasonal_card,
    clear_pending_seasonal_card,
    decorate_seasonal_context_for_tracking,
    decorate_seasonal_cards_for_tracking,
    get_active_seasonal_card,
    seasonal_card_completion_state,
    seasonal_metric_payload_from_landing_args,
    set_active_seasonal_card,
)
from services.tax_package import build_tax_package_preview

REVIEW_FOCUS = ("receipt_required", "receipt_attach", "expense_confirm", "income_confirm", "done", "not_needed")
DEFAULT_REVIEW_FOCUS = "receipt_required"

REVIEW_SOURCE_LABELS = {
    "csv": "CSV 업로드",
    "popbill": "계좌 연동",
    "manual": "수기 입력",
    "seed": "샘플 데이터",
}


def parse_limit(value: str | None, default: int = 200) -> int:
    try:
        limit = int(value or default)
    except Exception:
        limit = default
    return max(20, min(limit, 200))


def back_to_review(month_key: str, focus: str, q: str, *, limit: int | None = None, anchor_tx_id: int | None = None):
    params = {"month": month_key, "focus": focus, "q": q}
    account = safe_str(request.form.get("account") or request.args.get("account"), max_len=32)
    if account:
        params["account"] = account
    if limit:
        params["limit"] = int(limit)
    url = url_for("web_calendar.review", **params)
    if anchor_tx_id:
        url = f"{url}#tx-{int(anchor_tx_id)}"
    return redirect(url)


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


def _receipt_effect_nav_params_from_request() -> dict[str, str]:
    params: dict[str, str] = {}
    for key in RECEIPT_EFFECT_QUERY_KEYS:
        raw = request.args.get(key)
        if raw is not None and str(raw).strip() != "":
            params[key] = str(raw)
    return params


def next_review_tx_id(
    *,
    db,
    Transaction,
    IncomeLabel,
    ExpenseLabel,
    EvidenceItem,
    user_pk: int,
    focus: str,
    start_dt: datetime,
    end_dt: datetime,
    q: str,
    after_tx,
) -> int | None:
    base = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
    )

    if q:
        like = f"%{q}%"
        base = base.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))

    if focus in ("income_unknown", "income_confirm"):
        base = (
            base.filter(Transaction.direction == "in")
            .outerjoin(IncomeLabel, IncomeLabel.transaction_id == Transaction.id)
            .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
        )
    elif focus in ("expense_unknown", "expense_confirm"):
        base = (
            base.filter(Transaction.direction == "out")
            .outerjoin(ExpenseLabel, ExpenseLabel.transaction_id == Transaction.id)
            .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status.in_(("unknown", "mixed"))))
        )
    elif focus in ("evidence_required", "receipt_required"):
        base = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "required")
            .filter(EvidenceItem.status == "missing")
        )
    else:  # evidence_maybe / receipt_attach
        base = (
            base.join(EvidenceItem, EvidenceItem.transaction_id == Transaction.id)
            .filter(EvidenceItem.requirement == "maybe")
            .filter(EvidenceItem.status == "missing")
        )

    base = base.filter(
        or_(
            Transaction.occurred_at < after_tx.occurred_at,
            and_(Transaction.occurred_at == after_tx.occurred_at, Transaction.id < after_tx.id),
        )
    )

    nxt = base.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).first()
    return int(nxt.id) if nxt else None


def _parse_int(v: str | None) -> int | None:
    try:
        if v is None:
            return None
        s = str(v).replace(",", "").strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _trim_text(raw: Any, *, max_len: int = 240) -> str:
    text = safe_str(raw, max_len=max_len)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ellipsize(text: str, *, max_len: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def _source_display_label(source: str | None) -> str:
    code = _trim_text(source, max_len=32).lower()
    if not code:
        return "출처 미상"
    if code in REVIEW_SOURCE_LABELS:
        return REVIEW_SOURCE_LABELS[code]
    if code.startswith("bank"):
        return "계좌 연동"
    if code.startswith("card"):
        return "카드 연동"
    return code.upper()


def _review_time_display(occurred_at: Any) -> str:
    if occurred_at is None:
        return "시간 정보 없음"
    try:
        return occurred_at.strftime("%m-%d %H:%M")
    except Exception:
        return "시간 정보 없음"


def _build_review_display_fields(tx: Any, *, account_badge: dict[str, Any] | None = None) -> dict[str, Any]:
    counterparty = _trim_text(getattr(tx, "counterparty", ""), max_len=255)
    memo = _trim_text(getattr(tx, "memo", ""), max_len=500)
    source_label = _source_display_label(getattr(tx, "source", ""))
    title = counterparty or _ellipsize(memo, max_len=34) or source_label
    amount = _parse_int(getattr(tx, "amount_krw", 0)) or 0
    direction = _trim_text(getattr(tx, "direction", ""), max_len=8).lower()
    subtitle = "입금 거래" if direction == "in" else "지출 거래"

    account_name = ""
    if isinstance(account_badge, dict):
        account_name = _trim_text(account_badge.get("name", ""), max_len=80)
    if account_name in {"", "미지정", "선택 계좌"}:
        account_name = "계좌 정보 없음"

    memo_display = ""
    if memo:
        memo_short = _ellipsize(memo, max_len=72)
        if (not counterparty) or (counterparty.replace(" ", "") != memo_short.replace(" ", "")):
            memo_display = memo_short

    return {
        "display_title": title,
        "display_subtitle": subtitle,
        "display_time": _review_time_display(getattr(tx, "occurred_at", None)),
        "display_amount": int(amount),
        "display_account": account_name,
        "display_source": source_label,
        "display_memo": memo_display,
        "raw_counterparty": counterparty,
    }


def _parse_account_filter(user_pk: int, raw: str | None) -> tuple[str, int | None, str]:
    # querystring 폭주/비정상 입력으로 인한 분기 오작동 방지
    token = (safe_str(raw or "all", max_len=64) or "all").strip().lower()
    if token in ("", "all"):
        return "all", None, "all"
    if token == "unassigned":
        return "unassigned", None, "unassigned"
    try:
        account_id = int(token)
    except Exception:
        return "all", None, "all"
    if account_id <= 0:
        return "all", None, "all"
    try:
        owns = (
            UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
            .filter(UserBankAccount.id == int(account_id))
            .first()
        )
    except Exception:
        try:
            from core.extensions import db as _db

            _db.session.rollback()
        except Exception:
            pass
        return "all", None, "all"
    if not owns:
        return "all", None, "all"
    return "account", int(account_id), str(account_id)


def _apply_account_filter(query, mode: str, account_id: int | None):
    if mode == "account" and account_id:
        return query.filter(Transaction.bank_account_id == int(account_id))
    if mode == "unassigned":
        return query.filter(Transaction.bank_account_id.is_(None))
    return query


def _merchant_score(a: str, b: str) -> float:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _parse_receipt_draft(note: str | None) -> dict[str, Any]:
    text = str(note or "")
    if not text.startswith("receipt_draft:"):
        return {}
    try:
        payload = json.loads(text[len("receipt_draft:") :])
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _parse_receipt_paid_at(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in ("%Y-%m-%d", "%Y.%m.%d"):
                parsed = parsed.replace(hour=12, minute=0)
            return parsed
        except Exception:
            continue
    return None


def _score_receipt_candidates(
    *,
    rows: list[Any],
    total: int | None,
    merchant: str,
    paid_at_dt: datetime | None,
    current_tx_id: int,
) -> list[dict[str, Any]]:
    merchant_tokens = [
        tok for tok in re.split(r"[\s\-\(\)\[\]_/,.]+", str(merchant or "").lower()) if tok and len(tok) >= 2
    ][:4]

    scored: list[dict[str, Any]] = []
    for t in rows:
        s = 0.0
        reasons: list[str] = []
        amount_krw = int(t.amount_krw or 0)
        if total is not None:
            diff = abs(amount_krw - int(total))
            ratio = (diff / float(total)) if int(total) > 0 else 1.0
            if diff == 0:
                s += 2.2
                reasons.append("금액 일치")
            elif ratio <= 0.01:
                s += 1.6
                reasons.append("금액 ±1%")
            elif ratio <= 0.03:
                s += 1.1
                reasons.append("금액 ±3%")
            elif diff <= 5000:
                s += 0.6

        if paid_at_dt:
            day_diff = abs((t.occurred_at.date() - paid_at_dt.date()).days)
            if day_diff <= 1:
                s += 1.4
                reasons.append("날짜 ±1일")
            elif day_diff <= 3:
                s += 0.9
                reasons.append("날짜 ±3일")
            elif day_diff <= 7:
                s += 0.4

        cp = (t.counterparty or "")
        cp_lower = cp.lower()
        memo_lower = (t.memo or "").lower()
        merchant_ratio = _merchant_score(str(merchant or ""), cp)
        if merchant_ratio >= 0.92:
            s += 1.4
            reasons.append("거래처 유사")
        elif merchant_ratio >= 0.75:
            s += 0.9
        elif merchant_ratio >= 0.5:
            s += 0.4

        merchant_lower = str(merchant or "").lower()
        if merchant and (merchant_lower in cp_lower or cp_lower in merchant_lower):
            s += 0.6
            reasons.append("거래처 부분 일치")

        keyword_hits = 0
        for token in merchant_tokens:
            if token in cp_lower or token in memo_lower:
                keyword_hits += 1
        if keyword_hits > 0:
            s += min(0.6, keyword_hits * 0.2)

        if int(t.id) == int(current_tx_id):
            s += 0.25
            reasons.append("현재 거래")
        scored.append({"score": s, "tx": t, "reasons": reasons})

    scored.sort(key=lambda x: (x["score"], x["tx"].occurred_at, x["tx"].id), reverse=True)
    return scored


def _log_quick_match_metric(
    *,
    user_pk: int,
    db,
    ActionLog,
    event: str,
    month_key: str,
    tx_id: int | None = None,
    candidate_tx_id: int | None = None,
) -> None:
    if ActionLog is None:
        return
    event_name = str(event or "").strip().lower()
    if event_name not in {"quick_match_suggest_shown", "quick_match_confirmed", "quick_match_rejected", "quick_match_later"}:
        return
    ids = [int(v) for v in (tx_id, candidate_tx_id) if isinstance(v, int) and int(v) > 0]
    row = ActionLog(
        user_pk=int(user_pk),
        action_type="label_update",
        target_ids=ids,
        before_state={
            "metric_event": event_name,
            "month_key": str(month_key or ""),
            "tx_id": int(tx_id) if isinstance(tx_id, int) and tx_id > 0 else None,
            "candidate_tx_id": int(candidate_tx_id) if isinstance(candidate_tx_id, int) and candidate_tx_id > 0 else None,
        },
        after_state={},
        is_reverted=False,
    )
    try:
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _undo_label(payload: dict) -> str:
    kind = str(payload.get("kind") or "")
    if kind == "bulk":
        items = payload.get("items") or []
        count = len(items) if isinstance(items, list) else int(payload.get("count") or 0)
        return f"일괄 처리 되돌리기 ({count or 1}건)"
    tx_id = int(payload.get("tx_id") or 0)
    if kind == "income":
        return f"수입 분류 되돌리기 (tx #{tx_id})"
    if kind == "expense":
        return f"지출 분류 되돌리기 (tx #{tx_id})"
    if kind == "evidence":
        return f"증빙 상태 되돌리기 (tx #{tx_id})"
    if kind == "review_state":
        return f"보류 상태 되돌리기 (tx #{tx_id})"
    return f"최근 작업 되돌리기 (tx #{tx_id})"


def _detect_action_type(payload: dict, action_type: str | None = None) -> str:
    if action_type in ("label_update", "mark_unneeded", "attach", "bulk_update"):
        return action_type
    kind = str(payload.get("kind") or "")
    if kind == "evidence":
        return "attach"
    return "label_update"


def _get_review_undo_stack(
    *,
    user_pk: int | None = None,
    db=None,
    ActionLog=None,
    max_items: int = 10,
) -> list[dict]:
    if user_pk and db is not None and ActionLog is not None:
        rows = (
            db.session.query(ActionLog)
            .filter(ActionLog.user_pk == int(user_pk))
            .filter(ActionLog.is_reverted.is_(False))
            .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
            .limit(int(max_items))
            .all()
        )
        out: list[dict] = []
        for row in reversed(rows):
            before_state = row.before_state if isinstance(row.before_state, dict) else {}
            payload = before_state.get("payload")
            if not isinstance(payload, dict):
                continue
            item = dict(payload)
            item["id"] = str(int(row.id))
            if row.created_at:
                item["_created_at"] = row.created_at.strftime("%m-%d %H:%M")
            out.append(item)
        return out

    stack = session.get("review_undo_stack")
    if not isinstance(stack, list):
        legacy = session.get("review_undo")
        stack = [legacy] if isinstance(legacy, dict) else []

    changed = False
    normalized: list[dict] = []
    for item in stack:
        if not isinstance(item, dict):
            changed = True
            continue
        if not item.get("id"):
            item["id"] = uuid4().hex
            changed = True
        normalized.append(item)

    if changed or ("review_undo" in session):
        session["review_undo_stack"] = normalized
        session.pop("review_undo", None)
        session.modified = True

    return normalized


def _set_review_undo(
    payload: dict,
    *,
    user_pk: int | None = None,
    db=None,
    ActionLog=None,
    action_type: str | None = None,
    max_items: int = 10,
):
    if user_pk and db is not None and ActionLog is not None:
        tx_id = int(payload.get("tx_id") or 0)
        target_ids = [tx_id] if tx_id > 0 else []
        row = ActionLog(
            user_pk=int(user_pk),
            action_type=_detect_action_type(payload, action_type),
            target_ids=target_ids,
            before_state={"payload": payload},
            after_state={},
            is_reverted=False,
        )
        try:
            db.session.add(row)
            db.session.commit()
            return
        except Exception:
            db.session.rollback()

    stack = _get_review_undo_stack()
    if not payload.get("id"):
        payload["id"] = uuid4().hex
    stack.append(payload)
    if len(stack) > max_items:
        stack = stack[-max_items:]
    session["review_undo_stack"] = stack
    # backward compatibility cleanup
    if "review_undo" in session:
        session.pop("review_undo", None)
    session.modified = True


def _set_review_undo_many(
    payloads: list[dict],
    *,
    user_pk: int | None = None,
    db=None,
    ActionLog=None,
    action_type: str | None = "bulk_update",
    max_items: int = 10,
):
    items = [p for p in payloads if isinstance(p, dict)]
    if not items:
        return

    if user_pk and db is not None and ActionLog is not None:
        target_ids = sorted({int(p.get("tx_id") or 0) for p in items if int(p.get("tx_id") or 0) > 0})
        row = ActionLog(
            user_pk=int(user_pk),
            action_type=_detect_action_type({"kind": "bulk"}, action_type),
            target_ids=target_ids,
            before_state={
                "payload": {
                    "kind": "bulk",
                    "items": items,
                    "count": len(items),
                }
            },
            after_state={},
            is_reverted=False,
        )
        try:
            db.session.add(row)
            db.session.commit()
            return
        except Exception:
            db.session.rollback()

    payload = {"kind": "bulk", "items": items, "count": len(items)}
    _set_review_undo(payload, max_items=max_items)


def _apply_undo_payload(
    *,
    db,
    user_pk: int,
    Transaction,
    IncomeLabel,
    ExpenseLabel,
    EvidenceItem,
    payload: dict,
) -> int | None:
    kind = str(payload.get("kind") or "")
    if kind == "bulk":
        last_tx_id = None
        for one in payload.get("items") or []:
            if not isinstance(one, dict):
                continue
            reverted_tx = _apply_undo_payload(
                db=db,
                user_pk=user_pk,
                Transaction=Transaction,
                IncomeLabel=IncomeLabel,
                ExpenseLabel=ExpenseLabel,
                EvidenceItem=EvidenceItem,
                payload=one,
            )
            if reverted_tx:
                last_tx_id = reverted_tx
        return last_tx_id

    tx_id = int(payload.get("tx_id") or 0) or None
    prev = payload.get("prev") or {}
    if not tx_id:
        return None

    if kind == "review_state":
        tx = db.session.query(Transaction).filter_by(user_pk=user_pk, id=tx_id).first()
        if tx:
            tx.review_state = str(prev.get("review_state") or "todo")
            db.session.add(tx)
        return tx_id

    if kind == "income":
        row = db.session.query(IncomeLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not prev.get("exists"):
            if row:
                db.session.delete(row)
        else:
            if not row:
                row = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)
            row.status = prev.get("status") or "unknown"
            row.confidence = int(prev.get("confidence") or 0)
            row.labeled_by = prev.get("labeled_by") or "auto"
            db.session.add(row)
        return tx_id

    if kind == "expense":
        row = db.session.query(ExpenseLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not prev.get("label_exists"):
            if row:
                db.session.delete(row)
        else:
            if not row:
                row = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)
            row.status = prev.get("label_status") or "unknown"
            row.confidence = int(prev.get("label_confidence") or 0)
            row.labeled_by = prev.get("label_by") or "auto"
            db.session.add(row)

        ev = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not prev.get("evidence_exists"):
            if ev:
                db.session.delete(ev)
        else:
            if not ev:
                ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id)
            ev.requirement = prev.get("evidence_requirement") or "maybe"
            ev.status = prev.get("evidence_status") or "missing"
            db.session.add(ev)
        return tx_id

    if kind == "evidence":
        ev = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not prev.get("exists"):
            if ev:
                db.session.delete(ev)
        else:
            if not ev:
                ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id)
            ev.requirement = prev.get("requirement") or "maybe"
            ev.status = prev.get("status") or "missing"
            db.session.add(ev)
        return tx_id

    return tx_id


def register_review_routes(
    *,
    bp,
    uid_getter,
    parse_month,
    month_range,
    month_key_from_tx,
    is_partial,
    utcnow_fn,
    db,
    compute_tax_estimate,
    Transaction,
    IncomeLabel,
    ExpenseLabel,
    EvidenceItem,
    CounterpartyRule,
    CounterpartyExpenseRule,
    default_retention_until,
    delete_physical_file,
    resolve_file_path,
    store_evidence_file_multi,
    store_evidence_text_file,
    parse_receipt_from_file,
    parse_receipt_from_text,
    ActionLog=None,
    ReceiptExpenseFollowupAnswer=None,
    ReceiptExpenseReinforcement=None,
):
    def _compute_monthly_tax_estimate(user_pk: int, *, month_key: str):
        try:
            return compute_tax_estimate(
                user_pk,
                month_key=month_key,
                prefer_monthly_signal=True,
            )
        except TypeError:
            return compute_tax_estimate(user_pk, month_key=month_key)

    def _seasonal_click_url(metric_payload: dict[str, object], target_url: str) -> str:
        return url_for(
            "web_overview.seasonal_card_click",
            **build_seasonal_tracking_query_params(metric_payload, redirect_to=str(target_url or "")),
        )

    def _load_follow_up_answers_for_tx_ids(user_pk: int, tx_ids: list[int] | tuple[int, ...]) -> dict[int, dict[str, dict[str, Any]]]:
        if ReceiptExpenseFollowupAnswer is None:
            return {}
        return load_receipt_follow_up_answers_map(
            db.session,
            ReceiptExpenseFollowupAnswer,
            user_pk=int(user_pk),
            transaction_ids=list(tx_ids or []),
        )

    def _load_reinforcement_for_tx_ids(user_pk: int, tx_ids: list[int] | tuple[int, ...]) -> dict[int, dict[str, Any]]:
        if ReceiptExpenseReinforcement is None:
            return {}
        return load_receipt_reinforcement_map(
            db.session,
            ReceiptExpenseReinforcement,
            user_pk=int(user_pk),
            transaction_ids=list(tx_ids or []),
        )

    def _parse_receipt_draft_from_evidence(ev) -> tuple[dict[str, Any], str]:
        draft: dict[str, Any] = {}
        receipt_type = ""
        note = str(getattr(ev, "note", "") or "")
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
        return draft if isinstance(draft, dict) else {}, receipt_type

    def _follow_up_context_from_form() -> tuple[dict[str, Any], str]:
        draft = {
            "merchant": (request.form.get("merchant") or "").strip(),
            "paid_at": (request.form.get("paid_at") or "").strip(),
            "total_krw": (request.form.get("total_krw") or "").strip(),
            "vat_krw": (request.form.get("vat_krw") or "").strip(),
            "payment_method": (request.form.get("payment_method") or "").strip(),
            "card_tail": (request.form.get("card_tail") or "").strip(),
            "approval_no": (request.form.get("approval_no") or "").strip(),
        }
        if not any(str(v or "").strip() for v in draft.values()):
            draft = {}
        receipt_type = (request.form.get("receipt_type") or "").strip()
        return draft, receipt_type

    def _build_receipt_tax_effect_redirect_params(
        *,
        before_est,
        after_est,
        effect_level: str,
    ) -> dict[str, int | str]:
        tax_before = int(getattr(before_est, "tax_due_est_krw", 0) or 0)
        tax_after = int(getattr(after_est, "tax_due_est_krw", 0) or 0)
        buffer_before = int(getattr(before_est, "buffer_target_krw", 0) or 0)
        buffer_after = int(getattr(after_est, "buffer_target_krw", 0) or 0)
        expense_before = int(getattr(before_est, "expense_business_krw", 0) or 0)
        expense_after = int(getattr(after_est, "expense_business_krw", 0) or 0)
        profit_before = int(getattr(before_est, "estimated_profit_krw", 0) or 0)
        profit_after = int(getattr(after_est, "estimated_profit_krw", 0) or 0)
        return {
            "receipt_effect_event": 1,
            "receipt_effect_toast": 1,
            "receipt_effect_level": str(effect_level or ""),
            "current_tax_due_est_krw": tax_after,
            "current_buffer_target_krw": buffer_after,
            "tax_delta_from_receipts_krw": int(tax_after - tax_before),
            "buffer_delta_from_receipts_krw": int(buffer_after - buffer_before),
            "receipt_reflected_expense_krw": int(getattr(after_est, "receipt_reflected_expense_krw", 0) or 0),
            "receipt_pending_expense_krw": int(getattr(after_est, "receipt_pending_expense_krw", 0) or 0),
            "tax_before": tax_before,
            "tax_after": tax_after,
            "buffer_before": buffer_before,
            "buffer_after": buffer_after,
            "expense_before": expense_before,
            "expense_after": expense_after,
            "profit_before": profit_before,
            "profit_after": profit_after,
            "tax_delta": int(tax_after - tax_before),
        }

    def _redirect_after_follow_up_save(
        *,
        tx_id: int,
        month_key: str,
        focus: str,
        q: str,
        limit: int,
        return_view: str,
        extra_params: dict[str, int | str] | None = None,
    ):
        partial_value = "1" if (request.form.get("partial") or request.args.get("partial") or "").strip() == "1" else None
        if return_view == "confirm":
            return redirect(
                url_for(
                    "web_calendar.receipt_confirm_page",
                    tx_id=tx_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                    partial=partial_value,
                    **dict(extra_params or {}),
                )
            )
        if return_view == "match":
            return redirect(
                url_for(
                    "web_calendar.receipt_match_page",
                    tx_id=tx_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                    partial=partial_value,
                    **dict(extra_params or {}),
                )
            )
        params = {"month": month_key, "focus": focus, "q": q}
        account = safe_str(request.form.get("account") or request.args.get("account"), max_len=32)
        if account:
            params["account"] = account
        if limit:
            params["limit"] = int(limit)
        params.update({k: v for k, v in dict(extra_params or {}).items() if v is not None})
        url = url_for("web_calendar.review", **params)
        return redirect(f"{url}#tx-{int(tx_id)}")

    @bp.get("/reconcile")
    def reconcile():
        user_pk = uid_getter()
        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")
        account_mode, account_filter_id, account_filter_value = _parse_account_filter(user_pk, request.args.get("account"))
        account_options = list_accounts_for_ui(
            user_pk,
            keep_ids=([account_filter_id] if account_mode == "account" and account_filter_id else None),
        )
        account_filter_name = "전체 계좌"
        if account_mode == "unassigned":
            account_filter_name = "미지정"
        elif account_mode == "account" and account_filter_id:
            selected_account = next((x for x in account_options if int(x.get("id") or 0) == int(account_filter_id)), None)
            if selected_account:
                account_filter_name = str(selected_account.get("display_name") or "선택 계좌")

        def _scoped(query):
            return _apply_account_filter(query, account_mode, account_filter_id)

        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        prev_month = (month_first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m")
        next_month = (month_first.replace(day=28) + timedelta(days=10)).replace(day=1).strftime("%Y-%m")

        tx_total = (
            _scoped(
                db.session.query(func.count(Transaction.id))
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .scalar()
        ) or 0
        has_transactions = bool(int(tx_total or 0) > 0)

        income_total = (
            _scoped(
                db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "in")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .scalar()
        ) or 0

        expense_business_total = (
            _scoped(
                db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
                .select_from(Transaction)
                .join(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "out")
                .filter(ExpenseLabel.status == "business")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .scalar()
        ) or 0

        expense_personal_total = (
            _scoped(
                db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
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
                .filter(
                    or_(
                        ExpenseLabel.status == "personal",
                        EvidenceItem.status == "not_needed",
                        EvidenceItem.requirement == "not_needed",
                    )
                )
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .scalar()
        ) or 0

        non_income_total = (
            _scoped(
                db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
                .select_from(Transaction)
                .join(
                    IncomeLabel,
                    and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "in")
                .filter(IncomeLabel.status == "non_income")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .scalar()
        ) or 0

        missing_required_row = (
            _scoped(
                db.session.query(
                    func.count(EvidenceItem.id),
                    func.coalesce(func.sum(Transaction.amount_krw), 0),
                )
                .select_from(EvidenceItem)
                .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
                .filter(EvidenceItem.user_pk == user_pk)
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "out")
                .filter(EvidenceItem.requirement == "required")
                .filter(EvidenceItem.status == "missing")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            )
            .first()
        )
        missing_required_count = int((missing_required_row[0] if missing_required_row else 0) or 0)
        missing_required_amount = int((missing_required_row[1] if missing_required_row else 0) or 0)

        expense_unknown_count = (
            _scoped(
                db.session.query(func.count(func.distinct(Transaction.id)))
                .select_from(Transaction)
                .outerjoin(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "out")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                .filter(or_(ExpenseLabel.id.is_(None), ExpenseLabel.status == "unknown"))
            )
            .scalar()
        ) or 0

        mixed_count = (
            _scoped(
                db.session.query(func.count(func.distinct(Transaction.id)))
                .select_from(Transaction)
                .join(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "out")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                .filter(ExpenseLabel.status == "mixed")
            )
            .scalar()
        ) or 0

        income_unknown_count = (
            _scoped(
                db.session.query(func.count(func.distinct(Transaction.id)))
                .select_from(Transaction)
                .outerjoin(
                    IncomeLabel,
                    and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.direction == "in")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                .filter(or_(IncomeLabel.id.is_(None), IncomeLabel.status == "unknown"))
            )
            .scalar()
        ) or 0

        review_rows_raw = (
            _scoped(
                db.session.query(Transaction, IncomeLabel.status, ExpenseLabel.status)
                .select_from(Transaction)
                .outerjoin(
                    IncomeLabel,
                    and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                )
                .outerjoin(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                .filter(
                    or_(
                        and_(
                            Transaction.direction == "in",
                            or_(IncomeLabel.id.is_(None), IncomeLabel.status == "unknown"),
                        ),
                        and_(
                            Transaction.direction == "out",
                            or_(ExpenseLabel.id.is_(None), ExpenseLabel.status.in_(("unknown", "mixed"))),
                        ),
                    )
                )
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                .limit(20)
            )
            .all()
        )

        review_rows: list[dict] = []
        for tx, income_status, expense_status in review_rows_raw:
            if tx.direction == "in":
                state_text = "수입 확인 필요"
                reason_text = "수입/비수입 확정이 아직 필요해요."
            elif expense_status == "mixed":
                state_text = "혼합"
                reason_text = "업무/개인 안분 근거를 메모로 남겨주세요."
            else:
                state_text = "지출 분류 필요"
                reason_text = "업무/개인 중 하나로 확정이 필요해요."
            review_rows.append(
                {
                    "tx_id": int(tx.id),
                    "occurred_at": tx.occurred_at,
                    "counterparty": tx.counterparty or tx.memo or "알 수 없음",
                    "amount_krw": int(tx.amount_krw or 0),
                    "direction": tx.direction,
                    "state_text": state_text,
                    "reason_text": reason_text,
                }
            )

        preflight = {}
        duplicate_rows: list[dict] = []
        outlier_rows: list[dict] = []
        industry_hints: list[dict] = []
        try:
            zip_preview = build_tax_package_preview(user_pk=user_pk, month_key=month_key)
            preflight = (zip_preview or {}).get("preflight") or {}
            duplicate_rows = list(preflight.get("duplicate_suspects_preview") or [])[:20]
        except Exception:
            # 대사 페이지는 "요약 확인" 성격이므로 일부 보조 지표 실패 시에도
            # 전체 페이지를 깨지 않고 핵심 정리 동선은 유지한다.
            preflight = {}
            duplicate_rows = []
        try:
            outlier_rows = detect_large_transaction_outliers(
                user_pk=user_pk,
                month_key=month_key,
                lookback_days=90,
                limit=20,
            )
        except Exception:
            outlier_rows = []
        try:
            industry_hints = build_industry_missing_cost_hints(
                user_pk=user_pk,
                month_key=month_key,
                limit=3,
            )
        except Exception:
            industry_hints = []

        if account_mode in {"account", "unassigned"}:
            scoped_tx_ids = {
                int(tx_id)
                for (tx_id,) in (
                    _scoped(
                        db.session.query(Transaction.id)
                        .filter(Transaction.user_pk == user_pk)
                        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    ).all()
                )
                if tx_id
            }
            if duplicate_rows:
                duplicate_rows = [
                    row
                    for row in duplicate_rows
                    if int(row.get("tx_id_a") or 0) in scoped_tx_ids
                    or int(row.get("tx_id_b") or 0) in scoped_tx_ids
                ]
                if outlier_rows:
                    outlier_rows = [
                        row for row in outlier_rows if int((row or {}).get("tx_id") or 0) in scoped_tx_ids
                    ]

        # 대사 리포트의 리스트 항목에서도 거래별 계좌를 빠르게 식별할 수 있게 배지 정보 주입
        reconcile_tx_ids: set[int] = {
            int(row.get("tx_id") or 0) for row in review_rows if int(row.get("tx_id") or 0) > 0
        }
        for row in duplicate_rows:
            tx_id_a = int((row or {}).get("tx_id_a") or 0)
            tx_id_b = int((row or {}).get("tx_id_b") or 0)
            if tx_id_a > 0:
                reconcile_tx_ids.add(tx_id_a)
            if tx_id_b > 0:
                reconcile_tx_ids.add(tx_id_b)
        for row in outlier_rows:
            tx_id = int((row or {}).get("tx_id") or 0)
            if tx_id > 0:
                reconcile_tx_ids.add(tx_id)

        tx_account_map: dict[int, int] = {}
        if reconcile_tx_ids:
            tx_account_rows = (
                db.session.query(Transaction.id, Transaction.bank_account_id)
                .filter(Transaction.user_pk == user_pk)
                .filter(Transaction.id.in_(list(reconcile_tx_ids)))
                .all()
            )
            tx_account_map = {
                int(tx_id): int(bank_account_id or 0) for tx_id, bank_account_id in tx_account_rows if tx_id
            }

        reconcile_account_ids = sorted({account_id for account_id in tx_account_map.values() if account_id > 0})
        reconcile_account_options = list_accounts_for_ui(
            user_pk,
            include_hidden=True,
            keep_ids=(reconcile_account_ids or None),
        )
        reconcile_account_badge_map = {
            int(row.get("id") or 0): {
                "name": str(row.get("display_name") or "선택 계좌"),
                "color_hex": str(row.get("color_hex") or "#64748B"),
            }
            for row in reconcile_account_options
            if int(row.get("id") or 0) > 0
        }

        def _account_badge_for(account_id: int | None) -> dict[str, str]:
            acc_id = int(account_id or 0)
            if acc_id > 0:
                badge = reconcile_account_badge_map.get(acc_id)
                if badge:
                    return {
                        "name": str(badge.get("name") or "선택 계좌"),
                        "color_hex": str(badge.get("color_hex") or "#64748B"),
                    }
                return {"name": "선택 계좌", "color_hex": "#64748B"}
            return {"name": "미지정", "color_hex": "#64748B"}

        for row in review_rows:
            tx_id = int(row.get("tx_id") or 0)
            row["account_badge"] = _account_badge_for(tx_account_map.get(tx_id))
        for row in outlier_rows:
            tx_id = int((row or {}).get("tx_id") or 0)
            row["account_badge"] = _account_badge_for(tx_account_map.get(tx_id))
        for row in duplicate_rows:
            tx_id_a = int((row or {}).get("tx_id_a") or 0)
            tx_id_b = int((row or {}).get("tx_id_b") or 0)
            row["account_badge_a"] = _account_badge_for(tx_account_map.get(tx_id_a))
            row["account_badge_b"] = _account_badge_for(tx_account_map.get(tx_id_b))

        return render_template(
            "calendar/reconcile.html",
            month_key=month_key,
            month_first=month_first,
            prev_month=prev_month,
            next_month=next_month,
            has_transactions=has_transactions,
            tx_total=int(tx_total or 0),
            income_total=int(income_total or 0),
            expense_business_total=int(expense_business_total or 0),
            expense_personal_total=int(expense_personal_total or 0),
            non_income_total=int(non_income_total or 0),
            missing_required_count=int(missing_required_count),
            missing_required_amount=int(missing_required_amount),
            income_unknown_count=int(income_unknown_count or 0),
            expense_unknown_count=int(expense_unknown_count or 0),
            mixed_count=int(mixed_count or 0),
            review_rows=review_rows,
            duplicate_rows=duplicate_rows,
            outlier_rows=outlier_rows,
            industry_hints=industry_hints,
            preflight=preflight,
            account_filter_value=account_filter_value,
            account_filter_name=account_filter_name,
            account_options=account_options,
            required_url=url_for("web_calendar.review", month=month_key, lane="required", focus="receipt_required", q="", limit=30, account=(account_filter_value or None)),
            review_url=url_for("web_calendar.review", month=month_key, lane="review", focus="expense_confirm", q="", limit=30, account=(account_filter_value or None)),
            duplicate_url=url_for("web_calendar.review", month=month_key, lane="review", focus="receipt_attach", q="", limit=30, account=(account_filter_value or None)),
            import_url=url_for("web_inbox.import_page"),
        )

    @bp.get("/review")
    def review():
        user_pk = uid_getter()
        linked_accounts, linked_accounts_has_unavailable = get_linked_account_balances(user_pk, limit=6)

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")

        tax_est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        tax_recommended = int(tax_est.buffer_target_krw)
        health_insurance_buffer, health_insurance_note, nhis_payload = compute_nhis_monthly_buffer(
            user_pk=user_pk,
            month_key=month_key,
        )
        total_setaside_recommended = int(tax_recommended) + int(health_insurance_buffer)
        tax_balance = int(tax_est.buffer_total_krw)
        tax_shortage = max(0, tax_recommended - tax_balance)
        tax_overage = max(0, tax_balance - tax_recommended)

        tax_progress_pct = 0
        if tax_recommended > 0:
            tax_progress_pct = int(min(100, max(0, (tax_balance / tax_recommended) * 100)))

        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        rows_need_ev = (
            db.session.query(
                Transaction.id,
                ExpenseLabel.status,
                EvidenceItem.id,
                EvidenceItem.requirement,
                EvidenceItem.status,
                EvidenceItem.file_key,
                EvidenceItem.deleted_at,
            )
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
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(or_(Transaction.review_state.is_(None), Transaction.review_state != "hold"))
            .all()
        )

        def _defaults(expense_status: str | None) -> tuple[str, str]:
            if expense_status == "business":
                return "required", "missing"
            if expense_status == "personal":
                return "not_needed", "not_needed"
            return "maybe", "missing"

        created = 0
        updated = 0
        seen_tx_ids: set[int] = set()
        for tx_id, exp_status, ev_id, ev_req, ev_st, ev_file_key, ev_deleted_at in rows_need_ev:
            tx_id_int = int(tx_id)
            if tx_id_int in seen_tx_ids:
                continue
            seen_tx_ids.add(tx_id_int)
            req, st = _defaults(exp_status)

            if ev_id is None:
                existing_ev = (
                    db.session.query(EvidenceItem.id)
                    .filter(EvidenceItem.user_pk == user_pk, EvidenceItem.transaction_id == tx_id_int)
                    .first()
                )
                if existing_ev:
                    continue
                db.session.add(EvidenceItem(user_pk=user_pk, transaction_id=tx_id_int, requirement=req, status=st, note=None))
                created += 1
                continue

            has_file = bool(ev_file_key) and (ev_deleted_at is None)
            if ev_st == "attached" or has_file:
                continue
            if ev_req == "not_needed" and ev_st == "not_needed":
                continue

            if (ev_req != req) or (ev_st != st):
                ev = db.session.query(EvidenceItem).filter_by(id=ev_id, user_pk=user_pk).first()
                if ev:
                    ev.requirement = req
                    ev.status = st
                    db.session.add(ev)
                    updated += 1

        if created or updated:
            db.session.commit()

        requested_focus = (request.args.get("focus") or "").strip()
        requested_lane = (request.args.get("lane") or "").strip()
        if requested_lane not in ("required", "review", "done", "not_needed", "hold"):
            requested_lane = ""

        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)
        account_mode, account_filter_id, account_filter_value = _parse_account_filter(user_pk, request.args.get("account"))
        account_options = list_accounts_for_ui(
            user_pk,
            keep_ids=([account_filter_id] if account_mode == "account" and account_filter_id else None),
        )
        account_filter_name = "전체"
        if account_mode == "unassigned":
            account_filter_name = "미지정"
        elif account_mode == "account" and account_filter_id:
            selected_account = next((x for x in account_options if int(x.get("id") or 0) == int(account_filter_id)), None)
            if selected_account:
                account_filter_name = str(selected_account.get("display_name") or "선택 계좌")

        not_hold_filter = or_(Transaction.review_state.is_(None), Transaction.review_state != "hold")
        hold_filter = (Transaction.review_state == "hold")

        base_all = _apply_account_filter(
            (
            db.session.query(Transaction)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            ),
            account_mode,
            account_filter_id,
        )
        if q:
            like = f"%{q}%"
            base_all = base_all.filter((Transaction.counterparty.ilike(like)) | (Transaction.memo.ilike(like)))
        base = base_all.filter(not_hold_filter)

        income_need = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(Transaction.direction == "in")
                    .outerjoin(
                        IncomeLabel,
                        and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                    )
                    .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0

        receipt_required_missing = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(Transaction.direction == "out")
                    .join(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(EvidenceItem.requirement == "required")
                    .filter(EvidenceItem.status == "missing")
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0

        receipt_attach_missing = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(Transaction.direction == "out")
                    .join(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(EvidenceItem.requirement == "maybe")
                    .filter(EvidenceItem.status == "missing")
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0

        expense_confirm_need = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(Transaction.direction == "out")
                    .outerjoin(
                        ExpenseLabel,
                        and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                    )
                    .outerjoin(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status.in_(("unknown", "mixed"))))
                    .filter(or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)))
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0

        counts = {
            "receipt_required": int(receipt_required_missing or 0),
            "receipt_attach": int(receipt_attach_missing or 0),
            "expense_confirm": int(expense_confirm_need or 0),
            "income_confirm": int(income_need or 0),
        }
        not_needed_count = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(Transaction.direction == "out")
                    .outerjoin(
                        ExpenseLabel,
                        and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                    )
                    .outerjoin(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(
                        or_(
                            ExpenseLabel.status == "personal",
                            EvidenceItem.status == "not_needed",
                            EvidenceItem.requirement == "not_needed",
                        )
                    )
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0

        review_total_count = int(receipt_attach_missing or 0) + int(expense_confirm_need or 0) + int(income_need or 0)
        done_count = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .outerjoin(
                        IncomeLabel,
                        and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                    )
                    .outerjoin(
                        ExpenseLabel,
                        and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                    )
                    .outerjoin(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(
                        or_(
                            and_(Transaction.direction == "in", IncomeLabel.status.in_(("income", "non_income"))),
                            and_(
                                Transaction.direction == "out",
                                ExpenseLabel.status == "business",
                                or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)),
                                or_(EvidenceItem.status.is_(None), EvidenceItem.status != "not_needed"),
                                or_(EvidenceItem.requirement.is_(None), EvidenceItem.requirement != "not_needed"),
                            ),
                        )
                    )
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0
        hold_count = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(hold_filter)
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0
        lane_counts = {
            "required": int(receipt_required_missing or 0),
            "review": int(review_total_count),
            "done": int(done_count),
            "not_needed": int(not_needed_count or 0),
            "hold": int(hold_count or 0),
        }

        evidence_required_total = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .join(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.direction == "out")
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(EvidenceItem.requirement.in_(("required", "maybe")))
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0
        evidence_attached_total = (
            _apply_account_filter(
                (
                    db.session.query(func.count(func.distinct(Transaction.id)))
                    .select_from(Transaction)
                    .join(
                        EvidenceItem,
                        and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                    )
                    .filter(Transaction.user_pk == user_pk)
                    .filter(Transaction.direction == "out")
                    .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                    .filter(not_hold_filter)
                    .filter(EvidenceItem.requirement.in_(("required", "maybe")))
                    .filter(or_(EvidenceItem.status == "attached", and_(EvidenceItem.file_key.isnot(None), EvidenceItem.deleted_at.is_(None))))
                ),
                account_mode,
                account_filter_id,
            )
        ).scalar() or 0
        evidence_required_total = int(evidence_required_total or 0)
        evidence_attached_total = int(evidence_attached_total or 0)
        evidence_remaining_total = max(0, evidence_required_total - evidence_attached_total)
        evidence_completion = {
            "denominator": evidence_required_total,
            "numerator": evidence_attached_total,
            "remaining": evidence_remaining_total,
            "rate_pct": int(round((evidence_attached_total * 100.0 / evidence_required_total), 0))
            if evidence_required_total > 0
            else None,
            "has_target": bool(evidence_required_total > 0),
        }

        quick_match_events = {
            "shown": 0,
            "confirmed": 0,
            "rejected": 0,
            "later": 0,
        }
        if ActionLog is not None:
            try:
                metric_rows = (
                    db.session.query(ActionLog.before_state)
                    .filter(ActionLog.user_pk == user_pk)
                    .filter(ActionLog.created_at >= start_dt, ActionLog.created_at < end_dt)
                    .filter(ActionLog.action_type == "label_update")
                    .limit(800)
                    .all()
                )
                for (before_state,) in metric_rows:
                    if not isinstance(before_state, dict):
                        continue
                    event_name = str(before_state.get("metric_event") or "").strip().lower()
                    if event_name == "quick_match_suggest_shown":
                        quick_match_events["shown"] += 1
                    elif event_name == "quick_match_confirmed":
                        quick_match_events["confirmed"] += 1
                    elif event_name == "quick_match_rejected":
                        quick_match_events["rejected"] += 1
                    elif event_name == "quick_match_later":
                        quick_match_events["later"] += 1
            except Exception:
                quick_match_events = {
                    "shown": 0,
                    "confirmed": 0,
                    "rejected": 0,
                    "later": 0,
                }

        quick_sample_count = int(quick_match_events["confirmed"] + quick_match_events["rejected"])
        quick_accuracy_pct = None
        if quick_sample_count >= 10:
            quick_accuracy_pct = int(round((quick_match_events["confirmed"] * 100.0 / quick_sample_count), 0))

        quick_match_metrics = {
            "matched_count": evidence_attached_total,
            "pending_count": evidence_remaining_total,
            "match_suggest_shown": int(quick_match_events["shown"]),
            "match_confirmed": int(quick_match_events["confirmed"]),
            "match_rejected": int(quick_match_events["rejected"]),
            "match_later": int(quick_match_events["later"]),
            "accuracy_pct": quick_accuracy_pct,
            "sample_count": quick_sample_count,
        }

        quick_match_card = None
        try:
            quick_row = (
                _apply_account_filter(
                    (
                        db.session.query(Transaction, EvidenceItem)
                        .join(
                            EvidenceItem,
                            and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                        )
                        .filter(Transaction.user_pk == user_pk)
                        .filter(Transaction.direction == "out")
                        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                        .filter(not_hold_filter)
                        .filter(EvidenceItem.file_key.isnot(None))
                        .filter(EvidenceItem.deleted_at.is_(None))
                        .filter(EvidenceItem.note.like("receipt_draft:%"))
                    ),
                    account_mode,
                    account_filter_id,
                )
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                .first()
            )
            if quick_row:
                source_tx, source_ev = quick_row
                draft = _parse_receipt_draft(getattr(source_ev, "note", ""))
                total = _parse_int(str(draft.get("total_krw") or ""))
                merchant = str(draft.get("merchant") or "").strip()
                paid_at_dt = _parse_receipt_paid_at(draft.get("paid_at"))

                search_start = start_dt
                search_end = end_dt
                if paid_at_dt:
                    search_start = datetime.combine((paid_at_dt - timedelta(days=7)).date(), time.min)
                    search_end = datetime.combine((paid_at_dt + timedelta(days=8)).date(), time.min)

                candidate_rows = (
                    _apply_account_filter(
                        (
                            db.session.query(Transaction)
                            .filter(Transaction.user_pk == user_pk)
                            .filter(Transaction.direction == "out")
                            .filter(Transaction.occurred_at >= search_start, Transaction.occurred_at < search_end)
                            .filter(not_hold_filter)
                        ),
                        account_mode,
                        account_filter_id,
                    )
                    .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                    .limit(220)
                    .all()
                )
                scored = _score_receipt_candidates(
                    rows=candidate_rows,
                    total=total,
                    merchant=merchant,
                    paid_at_dt=paid_at_dt,
                    current_tx_id=int(source_tx.id),
                )
                if scored and float(scored[0]["score"]) >= 0.8:
                    best = scored[0]
                    candidate_tx = best["tx"]
                    reason_text = " · ".join(list(best["reasons"])[:3]) if isinstance(best["reasons"], list) else ""
                    candidate_label_row = ExpenseLabel.query.filter_by(user_pk=user_pk, transaction_id=int(candidate_tx.id)).first()
                    candidate_expense_kind = (
                        str(candidate_label_row.status or "").strip().lower() if candidate_label_row else ""
                    )
                    if candidate_expense_kind not in {"business", "personal", "mixed"}:
                        candidate_expense_kind = "mixed"
                    quick_match_card = {
                        "source_tx_id": int(source_tx.id),
                        "candidate_tx_id": int(candidate_tx.id),
                        "candidate_date": candidate_tx.occurred_at.strftime("%m-%d"),
                        "candidate_counterparty": (candidate_tx.counterparty or candidate_tx.memo or "알 수 없음"),
                        "candidate_amount_krw": int(candidate_tx.amount_krw or 0),
                        "candidate_expense_kind": candidate_expense_kind,
                        "evidence_filename": str(source_ev.original_filename or "증빙 파일"),
                        "evidence_is_image": bool(str(source_ev.mime_type or "").lower().startswith("image/")),
                        "score": round(float(best["score"]), 2),
                        "reason_text": reason_text,
                    }
                    seen_key = f"{month_key}:{int(source_tx.id)}:{int(candidate_tx.id)}"
                    if str(session.get("quick_match_seen_key") or "") != seen_key:
                        _log_quick_match_metric(
                            user_pk=user_pk,
                            db=db,
                            ActionLog=ActionLog,
                            event="quick_match_suggest_shown",
                            month_key=month_key,
                            tx_id=int(source_tx.id),
                            candidate_tx_id=int(candidate_tx.id),
                        )
                        session["quick_match_seen_key"] = seen_key
                        session.modified = True
        except Exception:
            quick_match_card = None

        onboarding_goal = get_primary_goal(user_pk)
        if requested_lane == "required":
            focus = "receipt_required"
        elif requested_lane == "done":
            focus = "done"
        elif requested_lane == "not_needed":
            focus = "not_needed"
        elif requested_lane == "hold":
            focus = "hold"
        elif requested_lane == "review":
            if requested_focus in ("receipt_attach", "expense_confirm", "income_confirm"):
                focus = requested_focus
            else:
                review_focus_orders = {
                    "tax_ready": ("expense_confirm", "income_confirm", "receipt_attach"),
                    "evidence_clean": ("receipt_attach", "expense_confirm", "income_confirm"),
                    "faster_month_close": ("expense_confirm", "receipt_attach", "income_confirm"),
                }
                order = review_focus_orders.get(onboarding_goal or "", ("expense_confirm", "income_confirm", "receipt_attach"))
                focus = next((k for k in order if int(counts.get(k, 0) or 0) > 0), order[0])
        elif requested_focus == "hold":
            focus = "hold"
        elif requested_focus in REVIEW_FOCUS:
            focus = requested_focus
        else:
            focus = pick_focus_from_counts(counts, onboarding_goal, default_focus=DEFAULT_REVIEW_FOCUS)

        if focus == "receipt_required":
            lane = "required"
        elif focus in ("receipt_attach", "expense_confirm", "income_confirm"):
            lane = "review"
        elif focus == "done":
            lane = "done"
        elif focus == "hold":
            lane = "hold"
        else:
            lane = "not_needed"

        prompt_action = (request.args.get("tax_profile_prompt") or "").strip()
        if prompt_action == "later":
            session["tax_profile_prompt_review_hidden"] = True
            session.modified = True
            return redirect(url_for("web_calendar.review", month=month_key, lane=lane, focus=focus, q=q, limit=limit))

        tax_profile_meta = tax_profile_completion_meta(user_pk)
        user_tx_count = (
            db.session.query(func.count(Transaction.id))
            .filter(Transaction.user_pk == user_pk)
            .scalar()
        ) or 0
        is_tax_profile_done = bool(tax_profile_meta["is_complete"])
        if is_tax_profile_done and session.get("tax_profile_prompt_review_hidden"):
            session.pop("tax_profile_prompt_review_hidden", None)
            session.modified = True
        prompt_hidden_this_session = bool(session.get("tax_profile_prompt_review_hidden"))
        show_tax_profile_prompt = bool(user_tx_count > 0 and (not is_tax_profile_done) and (not prompt_hidden_this_session))
        review_return_url = url_for("web_calendar.review", month=month_key, lane=lane, focus=focus, q=q, limit=limit)
        tax_profile_input_url = url_for(
            "web_profile.tax_profile",
            step=2,
            next=review_return_url,
            return_to_next=1,
            recovery_source="review_accuracy_card",
        )
        nhis_input_url = url_for("web_profile.nhis_page", month=month_key)
        tax_profile_later_url = url_for(
            "web_calendar.review",
            month=month_key,
            lane=lane,
            focus=focus,
            q=q,
            limit=limit,
            tax_profile_prompt="later",
        )

        biz_missing_amt = (
            db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
            .select_from(Transaction)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(not_hold_filter)
            .outerjoin(
                ExpenseLabel,
                and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
            )
            .outerjoin(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
            )
            .filter(ExpenseLabel.status == "business")
            .filter(EvidenceItem.status == "missing")
        ).scalar() or 0

        items: list[dict] = []
        seen_tx_ids: set[int] = set()
        title = ""

        if focus == "income_confirm":
            title = "수입 확인"
            query = (
                base.filter(Transaction.direction == "in")
                .outerjoin(
                    IncomeLabel,
                    and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                )
                .filter((IncomeLabel.id.is_(None)) | (IncomeLabel.status == "unknown"))
                .with_entities(Transaction, IncomeLabel.status, IncomeLabel.confidence, IncomeLabel.labeled_by)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, status, conf, by in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "income_confirm",
                        "reason": "입금 내역이 수입인지 아직 확정되지 않았어요",
                        "label_status": status or "unknown",
                        "confidence": int(conf or 0),
                        "labeled_by": by or "auto",
                    }
                )

        elif focus == "expense_confirm":
            title = "업무/개인 확정"
            query = (
                base.filter(Transaction.direction == "out")
                .outerjoin(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .outerjoin(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status.in_(("unknown", "mixed"))))
                .filter(or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)))
                .with_entities(Transaction, ExpenseLabel.status, EvidenceItem.status)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, exp_status, ev_status in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "expense_confirm",
                        "reason": "영수증(증빙)이 있어요 -> 업무/개인만 고르면 끝나요",
                        "expense_status": exp_status or "unknown",
                        "evidence_status": ev_status or "",
                    }
                )

        elif focus == "receipt_required":
            title = "영수증 꼭 필요"
            query = (
                base.filter(Transaction.direction == "out")
                .join(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter(EvidenceItem.requirement == "required")
                .filter(EvidenceItem.status == "missing")
                .with_entities(Transaction, EvidenceItem.requirement, EvidenceItem.status)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, req, st in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "receipt_required",
                        "reason": "업무 지출로 확정됐어요 -> 영수증이 꼭 필요해요",
                        "requirement": req,
                        "evidence_status": st,
                    }
                )

        elif focus == "receipt_attach":
            title = "영수증 붙이면 끝"
            query = (
                base.filter(Transaction.direction == "out")
                .join(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter(EvidenceItem.requirement == "maybe")
                .filter(EvidenceItem.status == "missing")
                .with_entities(Transaction, EvidenceItem.requirement, EvidenceItem.status)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, req, st in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "receipt_attach",
                        "reason": "업무/개인 결론이 애매해요 -> 영수증을 붙이면 바로 확정돼요",
                        "requirement": req,
                        "evidence_status": st,
                    }
                )

        elif focus == "done":
            title = "완료"
            query = (
                base.outerjoin(
                    IncomeLabel,
                    and_(IncomeLabel.transaction_id == Transaction.id, IncomeLabel.user_pk == user_pk),
                )
                .outerjoin(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .outerjoin(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter(
                    or_(
                        and_(Transaction.direction == "in", IncomeLabel.status.in_(("income", "non_income"))),
                        and_(
                            Transaction.direction == "out",
                            ExpenseLabel.status == "business",
                            or_(EvidenceItem.status == "attached", EvidenceItem.file_key.isnot(None)),
                            or_(EvidenceItem.status.is_(None), EvidenceItem.status != "not_needed"),
                            or_(EvidenceItem.requirement.is_(None), EvidenceItem.requirement != "not_needed"),
                        ),
                    )
                )
                .with_entities(Transaction, ExpenseLabel.status, EvidenceItem.status, EvidenceItem.requirement)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, exp_status, ev_status, ev_req in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "done",
                        "reason": "이번 달 기준으로 처리 완료된 거래예요",
                        "expense_status": exp_status or "",
                        "evidence_status": ev_status or "",
                        "requirement": ev_req or "",
                    }
                )

        elif focus == "hold":
            title = "보류"
            query = (
                base_all.filter(hold_filter)
                .with_entities(Transaction)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "hold",
                        "reason": "아직 확정이 어려워 잠시 보류한 거래예요",
                    }
                )

        else:  # not_needed
            title = "불필요"
            query = (
                base.filter(Transaction.direction == "out")
                .outerjoin(
                    ExpenseLabel,
                    and_(ExpenseLabel.transaction_id == Transaction.id, ExpenseLabel.user_pk == user_pk),
                )
                .outerjoin(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter(
                    or_(
                        ExpenseLabel.status == "personal",
                        EvidenceItem.status == "not_needed",
                        EvidenceItem.requirement == "not_needed",
                    )
                )
                .with_entities(Transaction, ExpenseLabel.status, EvidenceItem.status, EvidenceItem.requirement)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            rows = query.limit(limit).all()
            for tx, exp_status, ev_status, ev_req in rows:
                tx_id = int(getattr(tx, "id", 0) or 0)
                if tx_id in seen_tx_ids:
                    continue
                seen_tx_ids.add(tx_id)
                items.append(
                    {
                        "tx": tx,
                        "kind": "not_needed",
                        "reason": "세금 계산에서 제외되거나 증빙이 불필요한 거래예요",
                        "expense_status": exp_status or "",
                        "evidence_status": ev_status or "",
                        "requirement": ev_req or "",
                    }
                )

        item_account_ids = sorted(
            {
                int(getattr(item.get("tx"), "bank_account_id", 0) or 0)
                for item in items
                if int(getattr(item.get("tx"), "bank_account_id", 0) or 0) > 0
            }
        )
        account_badge_options = list_accounts_for_ui(user_pk, include_hidden=True, keep_ids=item_account_ids)
        account_badge_map = {
            int(row.get("id") or 0): {
                "name": str(row.get("display_name") or "선택 계좌"),
                "color_hex": str(row.get("color_hex") or "#64748B"),
            }
            for row in account_badge_options
            if int(row.get("id") or 0) > 0
        }
        follow_up_answer_map = _load_follow_up_answers_for_tx_ids(
            user_pk,
            [
                int(getattr(item.get("tx"), "id", 0) or 0)
                for item in items
                if int(getattr(item.get("tx"), "id", 0) or 0) > 0
            ],
        )
        reinforcement_map = _load_reinforcement_for_tx_ids(
            user_pk,
            [
                int(getattr(item.get("tx"), "id", 0) or 0)
                for item in items
                if int(getattr(item.get("tx"), "id", 0) or 0) > 0
            ],
        )
        for item in items:
            tx = item.get("tx")
            account_id = int(getattr(tx, "bank_account_id", 0) or 0)
            if account_id > 0 and account_id in account_badge_map:
                item["account_badge"] = {
                    "name": account_badge_map[account_id]["name"],
                    "color_hex": account_badge_map[account_id]["color_hex"],
                }
            elif account_id > 0:
                item["account_badge"] = {"name": "선택 계좌", "color_hex": "#64748B"}
            else:
                item["account_badge"] = {"name": "미지정", "color_hex": "#64748B"}
            item.update(
                _build_review_display_fields(
                    tx,
                    account_badge=item.get("account_badge"),
                )
            )
            if item.get("kind") in {"receipt_required", "receipt_attach", "expense_confirm"}:
                item_answers = follow_up_answer_map.get(int(getattr(tx, "id", 0) or 0), {})
                item_reinforcement = reinforcement_map.get(int(getattr(tx, "id", 0) or 0), {})
                item["expense_followup_answers"] = item_answers
                item["expense_reinforcement"] = item_reinforcement
                item["expense_guidance"] = build_receipt_expense_inline_guidance(
                    tx=tx,
                    focus_kind=str(item.get("kind") or ""),
                    follow_up_answers=item_answers,
                    reinforcement_data=item_reinforcement,
                )

        toast = (request.args.get("toast") or "").strip()
        undo_stack = _get_review_undo_stack(user_pk=user_pk, db=db, ActionLog=ActionLog, max_items=10)
        undo_item = undo_stack[-1] if undo_stack else None
        undo_count = len(undo_stack)
        undo_recent = [
            {"id": str(p.get("id") or ""), "label": _undo_label(p)}
            for p in reversed(undo_stack[-3:])
            if isinstance(p, dict)
        ]
        undo_recent_all = [
            {
                "id": str(p.get("id") or ""),
                "label": _undo_label(p),
                "time": str(p.get("_created_at") or ""),
            }
            for p in reversed(undo_stack[-10:])
            if isinstance(p, dict)
        ]

        def _int_arg(name: str) -> int | None:
            try:
                v = request.args.get(name)
                return int(v) if v is not None and v != "" else None
            except Exception:
                return None

        receipt_effect_nav_params = _receipt_effect_nav_params_from_request()
        review_calendar_url = url_for(
            "web_calendar.month_calendar",
            month=month_key,
            account=(account_filter_value or None),
            **receipt_effect_nav_params,
        )
        review_tax_buffer_url = url_for(
            "web_calendar.tax_buffer",
            month=month_key,
            account=(account_filter_value or None),
            **receipt_effect_nav_params,
        )
        seasonal_experience = build_seasonal_experience(
            user_pk=int(user_pk),
            month_key=month_key,
            urls={
                "review": url_for(
                    "web_calendar.review",
                    month=month_key,
                    lane="required",
                    focus="receipt_required",
                    q="",
                    limit=30,
                    account=(account_filter_value or None),
                ),
                "tax_buffer": review_tax_buffer_url,
                "package": url_for("web_package.page", month=month_key, account=(account_filter_value or None)),
                "profile": tax_profile_input_url,
            },
        )
        seasonal_experience = decorate_seasonal_cards_for_tracking(
            seasonal_experience,
            source_screen="review",
            month_key=month_key,
            click_url_builder=_seasonal_click_url,
        )
        seasonal_context = build_seasonal_screen_context(seasonal_experience, "review")
        seasonal_context = decorate_seasonal_context_for_tracking(
            seasonal_context,
            month_key=month_key,
            click_url_builder=_seasonal_click_url,
        )
        if seasonal_context and isinstance(seasonal_context.get("metric_payload"), dict):
            metric_payload = dict(seasonal_context.get("metric_payload") or {})
            record_seasonal_card_event(
                user_pk=int(user_pk),
                event="seasonal_card_shown",
                route="web_calendar.review",
                season_focus=str(metric_payload.get("season_focus") or ""),
                card_type=str(metric_payload.get("card_type") or ""),
                cta_target=str(metric_payload.get("cta_target") or ""),
                source_screen="review",
                priority=int(metric_payload.get("priority") or 0),
                completion_state_before=str(metric_payload.get("completion_state_before") or "todo"),
                month_key=str(metric_payload.get("month_key") or month_key),
            )
        landing_payload = seasonal_metric_payload_from_landing_args(request.args)
        if landing_payload and str(landing_payload.get("cta_target") or "") == "review":
            if str(landing_payload.get("completion_action") or ""):
                active_metric = activate_pending_seasonal_card(session) or set_active_seasonal_card(session, landing_payload)
            else:
                clear_pending_seasonal_card(session)
                active_metric = landing_payload
            record_seasonal_card_event(
                user_pk=int(user_pk),
                event="seasonal_card_landed",
                route="web_calendar.review",
                season_focus=str(active_metric.get("season_focus") or ""),
                card_type=str(active_metric.get("card_type") or ""),
                cta_target=str(active_metric.get("cta_target") or ""),
                source_screen=str(active_metric.get("source_screen") or "unknown"),
                priority=int(active_metric.get("priority") or 0),
                completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
                month_key=str(active_metric.get("month_key") or month_key),
            )

        return render_template(
            "calendar/review.html",
            month_first=month_first,
            month_key=month_key,
            lane=lane,
            focus=focus,
            title=title,
            q=q,
            limit=limit,
            counts=counts,
            totals=counts,
            lane_counts=lane_counts,
            items=items,
            tax_est=tax_est,
            tax_recommended=tax_recommended,
            tax_balance=tax_balance,
            tax_shortage=tax_shortage,
            tax_overage=tax_overage,
            tax_progress_pct=tax_progress_pct,
            biz_missing_amt=int(biz_missing_amt or 0),
            toast=toast,
            undo_item=undo_item,
            undo_count=undo_count,
            undo_recent=undo_recent,
            undo_recent_all=undo_recent_all,
            tax_before=_int_arg("tax_before"),
            tax_after=_int_arg("tax_after"),
            profit_before=_int_arg("profit_before"),
            profit_after=_int_arg("profit_after"),
            expense_before=_int_arg("expense_before"),
            expense_after=_int_arg("expense_after"),
            tax_delta=_int_arg("tax_delta"),
            receipt_effect_event=(request.args.get("receipt_effect_event") == "1"),
            receipt_effect_level=str(request.args.get("receipt_effect_level") or ""),
            current_tax_due_est_krw=_int_arg("current_tax_due_est_krw"),
            current_buffer_target_krw=_int_arg("current_buffer_target_krw"),
            tax_delta_from_receipts_krw=_int_arg("tax_delta_from_receipts_krw"),
            buffer_delta_from_receipts_krw=_int_arg("buffer_delta_from_receipts_krw"),
            receipt_reflected_expense_krw=_int_arg("receipt_reflected_expense_krw"),
            receipt_pending_expense_krw=_int_arg("receipt_pending_expense_krw"),
            show_tax_profile_prompt=show_tax_profile_prompt,
            tax_profile_input_url=tax_profile_input_url,
            nhis_input_url=nhis_input_url,
            tax_profile_later_url=tax_profile_later_url,
            tax_profile_meta=tax_profile_meta,
            health_insurance_buffer=int(health_insurance_buffer),
            health_insurance_note=(health_insurance_note or ""),
            nhis_payload=nhis_payload,
            total_setaside_recommended=int(total_setaside_recommended),
            linked_accounts=linked_accounts,
            linked_accounts_has_unavailable=bool(linked_accounts_has_unavailable),
            evidence_completion=evidence_completion,
            quick_match_metrics=quick_match_metrics,
            quick_match_card=quick_match_card,
            account_filter_value=account_filter_value,
            account_filter_name=account_filter_name,
            account_options=account_options,
            review_calendar_url=review_calendar_url,
            review_tax_buffer_url=review_tax_buffer_url,
            expense_guide_url=url_for("web_guide.expense_guide"),
            seasonal_context=seasonal_context,
            seasonal_experience=seasonal_experience,
        )

    @bp.post("/review/undo")
    def review_undo():
        user_pk = uid_getter()
        undo_id = safe_str(request.form.get("undo_id"), max_len=32)
        payload = None
        undo_row = None

        if ActionLog is not None:
            query = (
                db.session.query(ActionLog)
                .filter(ActionLog.user_pk == user_pk)
                .filter(ActionLog.is_reverted.is_(False))
            )
            if undo_id:
                try:
                    undo_row = query.filter(ActionLog.id == int(undo_id)).first()
                except Exception:
                    undo_row = None
            else:
                undo_row = query.order_by(ActionLog.created_at.desc(), ActionLog.id.desc()).first()
            if undo_row and isinstance(undo_row.before_state, dict):
                maybe_payload = undo_row.before_state.get("payload")
                if isinstance(maybe_payload, dict):
                    payload = maybe_payload
        else:
            stack = _get_review_undo_stack()
            if undo_id:
                for i in range(len(stack) - 1, -1, -1):
                    item = stack[i]
                    if isinstance(item, dict) and str(item.get("id") or "") == undo_id:
                        payload = stack.pop(i)
                        break
            else:
                payload = stack.pop() if stack else None
            session["review_undo_stack"] = stack
            session.pop("review_undo", None)
            session.modified = True

        month_key = parse_date_ym(request.form.get("month")) or utcnow_fn().strftime("%Y-%m")
        focus = safe_str(request.form.get("focus") or DEFAULT_REVIEW_FOCUS, max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)
        tx_id = None

        if not isinstance(payload, dict):
            flash("되돌릴 작업이 없어요.", "error")
            return back_to_review(month_key, focus, q, limit=limit)

        try:
            tx_id = _apply_undo_payload(
                db=db,
                user_pk=user_pk,
                Transaction=Transaction,
                IncomeLabel=IncomeLabel,
                ExpenseLabel=ExpenseLabel,
                EvidenceItem=EvidenceItem,
                payload=payload,
            )
            if undo_row:
                undo_row.is_reverted = True
                db.session.add(undo_row)
            db.session.commit()
            flash("방금 작업을 되돌렸어요.", "success")
        except Exception:
            db.session.rollback()
            flash("되돌리기에 실패했어요.", "error")

        return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=tx_id)

    @bp.post("/review/hold/<int:tx_id>")
    def review_hold(tx_id: int):
        user_pk = uid_getter()
        tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()

        month_key = parse_date_ym(request.form.get("month")) or (month_key_from_tx(tx) if tx else utcnow_fn().strftime("%Y-%m"))
        focus = safe_str(request.form.get("focus") or DEFAULT_REVIEW_FOCUS, max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)
        quick_event = safe_str(request.form.get("quick_match_event"), max_len=32).lower()
        quick_candidate_tx_id = _parse_int(request.form.get("quick_match_candidate_tx_id"))

        if not tx:
            flash("거래를 찾을 수 없어요.", "error")
            return back_to_review(month_key, focus, q, limit=limit)

        prev_state = str(tx.review_state or "todo")
        if prev_state == "hold":
            flash("이미 보류 상태예요.", "warn")
            return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=tx_id)

        try:
            tx.review_state = "hold"
            db.session.add(tx)
            db.session.commit()
            _set_review_undo(
                {
                    "kind": "review_state",
                    "tx_id": int(tx_id),
                    "prev": {"review_state": prev_state},
                },
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                action_type="label_update",
            )
            if quick_event == "later":
                _log_quick_match_metric(
                    user_pk=user_pk,
                    db=db,
                    ActionLog=ActionLog,
                    event="quick_match_later",
                    month_key=month_key,
                    tx_id=int(tx_id),
                    candidate_tx_id=int(quick_candidate_tx_id or 0),
                )
            flash("보류로 옮겼어요. 보류 탭에서 다시 처리할 수 있어요.", "success")
        except Exception:
            db.session.rollback()
            flash("보류 처리 중 문제가 발생했어요.", "error")

        return back_to_review(month_key, focus, q, limit=limit)

    @bp.post("/review/unhold/<int:tx_id>")
    def review_unhold(tx_id: int):
        user_pk = uid_getter()
        tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()

        month_key = parse_date_ym(request.form.get("month")) or (month_key_from_tx(tx) if tx else utcnow_fn().strftime("%Y-%m"))
        focus = safe_str(request.form.get("focus") or "hold", max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)

        if not tx:
            flash("거래를 찾을 수 없어요.", "error")
            return back_to_review(month_key, "hold", q, limit=limit)

        prev_state = str(tx.review_state or "todo")
        if prev_state != "hold":
            flash("이미 보류 해제 상태예요.", "warn")
            return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=tx_id)

        try:
            tx.review_state = "todo"
            db.session.add(tx)
            db.session.commit()
            _set_review_undo(
                {
                    "kind": "review_state",
                    "tx_id": int(tx_id),
                    "prev": {"review_state": "hold"},
                },
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                action_type="label_update",
            )
            flash("보류 해제했어요. 다시 정리 목록에서 확인해 주세요.", "success")
        except Exception:
            db.session.rollback()
            flash("보류 해제 중 문제가 발생했어요.", "error")

        return back_to_review(month_key, "hold", q, limit=limit)

    @bp.post("/review/bulk")
    def review_bulk():
        user_pk = uid_getter()

        action = safe_str(request.form.get("action"), max_len=20)
        account_filter_value = safe_str(request.form.get("account"), max_len=32)
        if action not in (
            "business",
            "personal",
            "not_needed",
            "mark_required",
            "mark_review",
            "attach_receipt",
            "assign_account",
        ):
            flash("일괄 처리값이 올바르지 않아요.", "error")
            return redirect(url_for("web_calendar.review"))

        month_key = parse_date_ym(request.form.get("month")) or utcnow_fn().strftime("%Y-%m")
        focus = safe_str(request.form.get("focus") or DEFAULT_REVIEW_FOCUS, max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        limit = parse_limit(request.form.get("limit"), default=200)

        raw_ids = [x for x in request.form.getlist("tx_ids") if str(x).strip()]
        if not raw_ids:
            raw_ids = [x.strip() for x in (request.form.get("selected_ids") or "").split(",") if x.strip()]

        tx_ids: list[int] = []
        seen_ids: set[int] = set()
        for raw in raw_ids:
            try:
                tid = int(str(raw).strip())
            except Exception:
                continue
            if tid <= 0 or tid in seen_ids:
                continue
            tx_ids.append(tid)
            seen_ids.add(tid)
            if len(tx_ids) >= 200:
                break

        if not tx_ids:
            flash("선택된 거래가 없어요.", "error")
            return back_to_review(month_key, focus, q, limit=limit)

        month_first = parse_month(month_key)
        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        tx_rows = (
            db.session.query(Transaction)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.id.in_(tx_ids))
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .all()
        )
        tx_map = {int(t.id): t for t in tx_rows}

        if action == "assign_account":
            selected_account_raw = safe_str(request.form.get("bulk_account_id"), max_len=32)
            new_alias = safe_str(request.form.get("bulk_new_account_alias"), max_len=64)
            target_account_id: int | None
            if selected_account_raw == "__new__":
                if not new_alias:
                    flash("새 계좌 별칭을 입력해 주세요.", "error")
                    return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit, account=(account_filter_value or None)))
                created = create_alias_account(user_pk=int(user_pk), alias=new_alias)
                target_account_id = int(created.id)
            elif selected_account_raw == "unassigned":
                target_account_id = None
            else:
                try:
                    candidate = int(selected_account_raw)
                except Exception:
                    candidate = 0
                if candidate <= 0:
                    flash("계좌를 선택해 주세요.", "error")
                    return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit, account=(account_filter_value or None)))
                owned = (
                    db.session.query(UserBankAccount.id)
                    .filter(UserBankAccount.user_pk == int(user_pk))
                    .filter(UserBankAccount.id == int(candidate))
                    .first()
                )
                if not owned:
                    flash("선택한 계좌를 찾을 수 없어요.", "error")
                    return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit, account=(account_filter_value or None)))
                target_account_id = int(candidate)

            applied_count = 0
            for tid in tx_ids:
                tx = tx_map.get(tid)
                if not tx:
                    continue
                tx.bank_account_id = int(target_account_id) if target_account_id else None
                db.session.add(tx)
                applied_count += 1
            try:
                db.session.commit()
                flash(f"선택한 거래 {applied_count}건의 계좌를 업데이트했어요.", "success")
            except Exception:
                db.session.rollback()
                flash("계좌 일괄 지정 중 문제가 발생했어요.", "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit, account=(account_filter_value or None)))

        if action == "attach_receipt":
            out_targets = [tid for tid in tx_ids if (tx_map.get(tid) and tx_map[tid].direction == "out")]
            if not out_targets:
                flash("영수증 첨부는 지출 거래를 선택했을 때 사용할 수 있어요.", "error")
                return back_to_review(month_key, focus, q, limit=limit)
            skipped = max(0, len(tx_ids) - len(out_targets))
            if len(out_targets) > 1:
                flash(f"{len(out_targets)}건 중 첫 항목부터 영수증 첨부를 시작해요.", "success")
            else:
                flash("선택한 항목의 영수증 첨부 화면으로 이동했어요.", "success")
            if skipped > 0:
                flash(f"입금 거래 {skipped}건은 첨부 대상에서 제외했어요.", "warn")
            return redirect(
                url_for(
                    "web_calendar.review_evidence_upload_page",
                    tx_id=int(out_targets[0]),
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                )
            )

        out_tx_ids = [tid for tid, tx in tx_map.items() if tx.direction == "out"]
        in_tx_ids = [tid for tid, tx in tx_map.items() if tx.direction == "in"]

        expense_labels = (
            db.session.query(ExpenseLabel)
            .filter(ExpenseLabel.user_pk == user_pk)
            .filter(ExpenseLabel.transaction_id.in_(out_tx_ids))
            .all()
            if out_tx_ids
            else []
        )
        expense_map = {int(r.transaction_id): r for r in expense_labels}

        income_labels = (
            db.session.query(IncomeLabel)
            .filter(IncomeLabel.user_pk == user_pk)
            .filter(IncomeLabel.transaction_id.in_(in_tx_ids))
            .all()
            if in_tx_ids
            else []
        )
        income_map = {int(r.transaction_id): r for r in income_labels}

        evidences = (
            db.session.query(EvidenceItem)
            .filter(EvidenceItem.user_pk == user_pk)
            .filter(EvidenceItem.transaction_id.in_(out_tx_ids))
            .all()
            if out_tx_ids
            else []
        )
        evidence_map = {int(r.transaction_id): r for r in evidences}

        done_count = 0
        failed_count = 0
        failed_reason = ""
        undo_payloads: list[dict] = []

        for tid in tx_ids:
            tx = tx_map.get(tid)
            if not tx:
                failed_count += 1
                failed_reason = failed_reason or "일부 항목을 찾을 수 없었어요."
                continue

            if action in ("business", "personal", "mark_required", "mark_review"):
                if tx.direction != "out":
                    failed_count += 1
                    failed_reason = failed_reason or "입금 거래에는 선택한 지출 일괄 처리를 적용할 수 없어요."
                    continue

                label = expense_map.get(tid)
                prev_expense = {
                    "label_exists": bool(label),
                    "label_status": (label.status if label else None),
                    "label_confidence": int(label.confidence or 0) if label else 0,
                    "label_by": (label.labeled_by if label else None),
                }
                if not label:
                    label = ExpenseLabel(user_pk=user_pk, transaction_id=tid)
                    expense_map[tid] = label

                if action in ("business", "mark_required"):
                    label.status = "business"
                elif action == "mark_review":
                    label.status = "mixed"
                else:
                    label.status = "personal"
                label.confidence = 100
                label.labeled_by = "user"
                label.decided_at = utcnow_fn()
                db.session.add(label)

                ev = evidence_map.get(tid)
                prev_expense["evidence_exists"] = bool(ev)
                prev_expense["evidence_requirement"] = (ev.requirement if ev else None)
                prev_expense["evidence_status"] = (ev.status if ev else None)
                if not ev:
                    ev = EvidenceItem(user_pk=user_pk, transaction_id=tid, requirement="maybe", status="missing", note=None)
                    evidence_map[tid] = ev

                has_file = bool(ev.file_key) and (ev.deleted_at is None)
                if action in ("business", "mark_required"):
                    ev.requirement = "required"
                    ev.status = "attached" if (has_file or ev.status == "attached") else "missing"
                elif action == "mark_review":
                    ev.requirement = "maybe"
                    ev.status = "attached" if (has_file or ev.status == "attached") else "missing"
                else:
                    ev.requirement = "not_needed"
                    ev.status = "not_needed"
                db.session.add(ev)

                undo_payloads.append({"kind": "expense", "tx_id": int(tid), "prev": prev_expense})
                done_count += 1
                continue

            # action == "not_needed"
            if tx.direction == "in":
                label = income_map.get(tid)
                prev_income = {
                    "exists": bool(label),
                    "status": (label.status if label else None),
                    "confidence": int(label.confidence or 0) if label else 0,
                    "labeled_by": (label.labeled_by if label else None),
                }
                if not label:
                    label = IncomeLabel(user_pk=user_pk, transaction_id=tid)
                    income_map[tid] = label

                label.status = "non_income"
                label.confidence = 100
                label.labeled_by = "user"
                label.decided_at = utcnow_fn()
                db.session.add(label)

                undo_payloads.append({"kind": "income", "tx_id": int(tid), "prev": prev_income})
                done_count += 1
                continue

            label = expense_map.get(tid)
            prev_expense = {
                "label_exists": bool(label),
                "label_status": (label.status if label else None),
                "label_confidence": int(label.confidence or 0) if label else 0,
                "label_by": (label.labeled_by if label else None),
            }
            if not label:
                label = ExpenseLabel(user_pk=user_pk, transaction_id=tid)
                expense_map[tid] = label

            label.status = "personal"
            label.confidence = 100
            label.labeled_by = "user"
            label.decided_at = utcnow_fn()
            db.session.add(label)

            ev = evidence_map.get(tid)
            prev_expense["evidence_exists"] = bool(ev)
            prev_expense["evidence_requirement"] = (ev.requirement if ev else None)
            prev_expense["evidence_status"] = (ev.status if ev else None)
            if not ev:
                ev = EvidenceItem(user_pk=user_pk, transaction_id=tid, requirement="not_needed", status="not_needed", note=None)
                evidence_map[tid] = ev
            else:
                ev.requirement = "not_needed"
                ev.status = "not_needed"
            db.session.add(ev)

            undo_payloads.append({"kind": "expense", "tx_id": int(tid), "prev": prev_expense})
            done_count += 1

        if done_count <= 0:
            msg = failed_reason or "처리할 수 있는 항목이 없었어요."
            flash(msg, "error")
            return back_to_review(month_key, focus, q, limit=limit)

        try:
            db.session.commit()
            _set_review_undo_many(
                undo_payloads,
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                action_type="bulk_update",
            )
        except IntegrityError:
            db.session.rollback()
            flash("일괄 저장 중 문제가 발생했어요.", "error")
            return back_to_review(month_key, focus, q, limit=limit)

        action_label = {
            "business": "업무",
            "personal": "개인",
            "not_needed": "불필요",
            "mark_required": "필수",
            "mark_review": "검토",
        }.get(action, "일괄")
        flash(f"{done_count}건을 '{action_label}'로 처리했어요. 필요하면 되돌리기로 복구할 수 있어요.", "success")
        if failed_count > 0:
            msg = failed_reason or "일부 항목은 처리하지 못했어요."
            flash(f"{failed_count}건은 처리하지 못했어요. {msg}", "error")

        return back_to_review(month_key, focus, q, limit=limit)

    @bp.post("/review/income/<int:tx_id>")
    def review_set_income(tx_id: int):
        user_pk = uid_getter()
        tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
        if not tx:
            flash("거래를 찾을 수 없어요.", "error")
            return redirect(url_for("web_calendar.month_calendar"))

        status = safe_str(request.form.get("status"), max_len=20)
        if status not in ("income", "non_income"):
            flash("처리값이 올바르지 않아요.", "error")
            return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

        always = parse_bool_yn(request.form.get("always")) is True
        apply_recent = parse_bool_yn(request.form.get("apply_recent", "1")) is not False
        focus = safe_str(request.form.get("focus") or "income_confirm", max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        month_key = parse_date_ym(request.form.get("month")) or month_key_from_tx(tx)
        limit = parse_limit(request.form.get("limit"), default=200)

        month_first = parse_month(month_key)
        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        label = db.session.query(IncomeLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        prev_income = {
            "exists": bool(label),
            "status": (label.status if label else None),
            "confidence": int(label.confidence or 0) if label else 0,
            "labeled_by": (label.labeled_by if label else None),
        }
        if not label:
            label = IncomeLabel(user_pk=user_pk, transaction_id=tx_id)

        label.status = status
        label.confidence = 100
        label.labeled_by = "user"
        label.decided_at = utcnow_fn()

        key = (tx.counterparty or "").strip() if always else None
        if key:
            rule = db.session.query(CounterpartyRule).filter_by(user_pk=user_pk, counterparty_key=key).first()
            if not rule:
                rule = CounterpartyRule(user_pk=user_pk, counterparty_key=key)
            rule.rule = status
            rule.active = True
            db.session.add(rule)

        try:
            db.session.add(label)
            db.session.commit()
            if not always:
                _set_review_undo(
                    {
                        "kind": "income",
                        "tx_id": int(tx_id),
                        "prev": prev_income,
                    },
                    user_pk=user_pk,
                    db=db,
                    ActionLog=ActionLog,
                    action_type="label_update",
                )
            if status == "income":
                flash("수입으로 확정했어요. 다음 항목으로 넘어갔어요.", "success")
            else:
                flash("비수입으로 확정했어요. 다음 항목으로 넘어갔어요.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("저장 중 문제가 발생했어요.", "error")

        if key:
            try:
                apply_start_dt = start_dt
                if apply_recent:
                    y = month_first.year
                    m = month_first.month - 2
                    while m <= 0:
                        y -= 1
                        m += 12
                    apply_start_dt = datetime(y, m, 1)

                tx_ids = [
                    r[0]
                    for r in (
                        db.session.query(Transaction.id)
                        .filter(Transaction.user_pk == user_pk)
                        .filter(Transaction.direction == "in")
                        .filter(Transaction.counterparty == key)
                        .filter(Transaction.occurred_at >= apply_start_dt, Transaction.occurred_at < end_dt)
                        .filter(Transaction.id != tx.id)
                        .all()
                    )
                ]

                applied = 0
                auto_undo_payloads: list[dict] = []
                if tx_ids:
                    labels = (
                        db.session.query(IncomeLabel)
                        .filter(IncomeLabel.user_pk == user_pk)
                        .filter(IncomeLabel.transaction_id.in_(tx_ids))
                        .all()
                    )
                    label_map = {l.transaction_id: l for l in labels}

                    for tid in tx_ids:
                        lab = label_map.get(tid)
                        if lab and lab.labeled_by == "user":
                            continue

                        prev_income = {
                            "exists": bool(lab),
                            "status": (lab.status if lab else None),
                            "confidence": int(lab.confidence or 0) if lab else 0,
                            "labeled_by": (lab.labeled_by if lab else None),
                        }
                        if not lab:
                            lab = IncomeLabel(user_pk=user_pk, transaction_id=tid)

                        if lab.status == status and lab.labeled_by != "user":
                            continue

                        lab.status = status
                        lab.confidence = 100
                        lab.labeled_by = "auto"
                        lab.decided_at = utcnow_fn()
                        db.session.add(lab)
                        applied += 1
                        auto_undo_payloads.append(
                            {
                                "kind": "income",
                                "tx_id": int(tid),
                                "prev": prev_income,
                            }
                        )

                    db.session.commit()

                if auto_undo_payloads:
                    _set_review_undo_many(
                        auto_undo_payloads,
                        user_pk=user_pk,
                        db=db,
                        ActionLog=ActionLog,
                        action_type="bulk_update",
                    )
                if applied > 0:
                    if apply_recent:
                        flash(f"같은 거래처 최근 3개월 {applied}건도 자동 분류했어요.", "success")
                    else:
                        flash(f"같은 거래처 이번 달 {applied}건도 자동 분류했어요.", "success")

            except Exception:
                db.session.rollback()

        next_id = next_review_tx_id(
            db=db,
            Transaction=Transaction,
            IncomeLabel=IncomeLabel,
            ExpenseLabel=ExpenseLabel,
            EvidenceItem=EvidenceItem,
            user_pk=user_pk,
            focus=focus,
            start_dt=start_dt,
            end_dt=end_dt,
            q=q,
            after_tx=tx,
        )
        return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)

    @bp.post("/review/expense/<int:tx_id>")
    def review_set_expense(tx_id: int):
        user_pk = uid_getter()
        tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
        if not tx:
            flash("거래를 찾을 수 없어요.", "error")
            return redirect(url_for("web_calendar.month_calendar"))

        status = safe_str(request.form.get("status"), max_len=20)
        if status not in ("business", "personal", "mixed"):
            flash("처리값이 올바르지 않아요.", "error")
            return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

        always = parse_bool_yn(request.form.get("always")) is True
        apply_recent = parse_bool_yn(request.form.get("apply_recent", "1")) is not False
        focus = safe_str(request.form.get("focus") or "expense_confirm", max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        month_key = parse_date_ym(request.form.get("month")) or month_key_from_tx(tx)
        limit = parse_limit(request.form.get("limit"), default=200)

        month_first = parse_month(month_key)
        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        label = db.session.query(ExpenseLabel).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        prev_expense = {
            "label_exists": bool(label),
            "label_status": (label.status if label else None),
            "label_confidence": int(label.confidence or 0) if label else 0,
            "label_by": (label.labeled_by if label else None),
        }
        if not label:
            label = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)

        label.status = status
        label.confidence = 100
        label.labeled_by = "user"
        label.decided_at = utcnow_fn()

        ev = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        prev_expense["evidence_exists"] = bool(ev)
        prev_expense["evidence_requirement"] = (ev.requirement if ev else None)
        prev_expense["evidence_status"] = (ev.status if ev else None)
        if not ev:
            ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement="maybe", status="missing", note=None)
            db.session.add(ev)
            db.session.flush()

        has_file = bool(ev.file_key) and (ev.deleted_at is None)

        if status == "business":
            ev.requirement = "required"
            ev.status = "attached" if (has_file or ev.status == "attached") else "missing"
        elif status == "personal":
            ev.requirement = "not_needed"
            ev.status = "not_needed"
        else:
            ev.requirement = "maybe"
            ev.status = "attached" if (has_file or ev.status == "attached") else "missing"

        db.session.add(ev)

        key = (tx.counterparty or "").strip() if (always and status in ("business", "personal")) else None
        if key:
            rule = db.session.query(CounterpartyExpenseRule).filter_by(user_pk=user_pk, counterparty_key=key).first()
            if not rule:
                rule = CounterpartyExpenseRule(user_pk=user_pk, counterparty_key=key)
            rule.rule = status
            rule.active = True
            db.session.add(rule)

        try:
            db.session.add(label)
            db.session.commit()
            if not always:
                _set_review_undo(
                    {
                        "kind": "expense",
                        "tx_id": int(tx_id),
                        "prev": prev_expense,
                    },
                    user_pk=user_pk,
                    db=db,
                    ActionLog=ActionLog,
                    action_type=("mark_unneeded" if status == "personal" else "label_update"),
                )
            if status == "business":
                flash("업무로 확정했어요. 필요하면 증빙 목록에서 바로 첨부할 수 있어요.", "success")
            elif status == "personal":
                flash("개인 지출로 처리했어요. 세금 계산에서 제외돼요.", "success")
            else:
                flash("혼합으로 분류했어요. 검토 목록에서 다시 확인할 수 있어요.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("저장 중 문제가 발생했어요.", "error")

        if key:
            try:
                apply_start_dt = start_dt
                if apply_recent:
                    y = month_first.year
                    m = month_first.month - 2
                    while m <= 0:
                        y -= 1
                        m += 12
                    apply_start_dt = datetime(y, m, 1)

                tx_ids = [
                    r[0]
                    for r in (
                        db.session.query(Transaction.id)
                        .filter(Transaction.user_pk == user_pk)
                        .filter(Transaction.direction == "out")
                        .filter(Transaction.counterparty == key)
                        .filter(Transaction.occurred_at >= apply_start_dt, Transaction.occurred_at < end_dt)
                        .filter(Transaction.id != tx.id)
                        .all()
                    )
                ]

                applied_labels = 0
                applied_evidence = 0
                auto_undo_payloads: list[dict] = []

                if tx_ids:
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

                    if status == "business":
                        req, ev_st = "required", "missing"
                    else:
                        req, ev_st = "not_needed", "not_needed"

                    for tid in tx_ids:
                        lab = label_map.get(tid)
                        if lab and lab.labeled_by == "user":
                            continue

                        ev2 = ev_map.get(tid)
                        prev_expense = {
                            "label_exists": bool(lab),
                            "label_status": (lab.status if lab else None),
                            "label_confidence": int(lab.confidence or 0) if lab else 0,
                            "label_by": (lab.labeled_by if lab else None),
                            "evidence_exists": bool(ev2),
                            "evidence_requirement": (ev2.requirement if ev2 else None),
                            "evidence_status": (ev2.status if ev2 else None),
                        }
                        changed = False

                        if not lab:
                            lab = ExpenseLabel(user_pk=user_pk, transaction_id=tid)

                        if not (lab.status == status and lab.labeled_by != "user"):
                            lab.status = status
                            lab.confidence = 100
                            lab.labeled_by = "auto"
                            lab.decided_at = utcnow_fn()
                            db.session.add(lab)
                            applied_labels += 1
                            changed = True

                        if not ev2:
                            ev2 = EvidenceItem(user_pk=user_pk, transaction_id=tid, requirement=req, status=ev_st, note=None)
                            db.session.add(ev2)
                            applied_evidence += 1
                            changed = True
                        else:
                            has_file2 = bool(ev2.file_key) and (ev2.deleted_at is None)
                            if ev2.status == "attached" or has_file2:
                                if changed:
                                    auto_undo_payloads.append(
                                        {
                                            "kind": "expense",
                                            "tx_id": int(tid),
                                            "prev": prev_expense,
                                        }
                                    )
                                continue
                            if ev2.requirement != req or ev2.status != ev_st:
                                ev2.requirement = req
                                ev2.status = ev_st
                                db.session.add(ev2)
                                applied_evidence += 1
                                changed = True

                        if changed:
                            auto_undo_payloads.append(
                                {
                                    "kind": "expense",
                                    "tx_id": int(tid),
                                    "prev": prev_expense,
                                }
                            )

                    db.session.commit()

                if auto_undo_payloads:
                    _set_review_undo_many(
                        auto_undo_payloads,
                        user_pk=user_pk,
                        db=db,
                        ActionLog=ActionLog,
                        action_type="bulk_update",
                    )
                if applied_labels > 0 or applied_evidence > 0:
                    if apply_recent:
                        flash(f"같은 거래처 최근 3개월 분류 {applied_labels}건 · 증빙 {applied_evidence}건도 정리했어요.", "success")
                    else:
                        flash(f"같은 거래처 이번 달 분류 {applied_labels}건 · 증빙 {applied_evidence}건도 정리했어요.", "success")

            except Exception:
                db.session.rollback()

        next_id = next_review_tx_id(
            db=db,
            Transaction=Transaction,
            IncomeLabel=IncomeLabel,
            ExpenseLabel=ExpenseLabel,
            EvidenceItem=EvidenceItem,
            user_pk=user_pk,
            focus=focus,
            start_dt=start_dt,
            end_dt=end_dt,
            q=q,
            after_tx=tx,
        )
        return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)

    @bp.post("/review/evidence/<int:tx_id>")
    def review_set_evidence(tx_id: int):
        user_pk = uid_getter()
        tx = db.session.query(Transaction).filter_by(id=tx_id, user_pk=user_pk).first()
        if not tx:
            flash("거래를 찾을 수 없어요.", "error")
            return redirect(url_for("web_calendar.month_calendar"))

        action = safe_str(request.form.get("action"), max_len=20)
        if action not in ("attached", "not_needed", "missing"):
            flash("처리값이 올바르지 않아요.", "error")
            return redirect(url_for("web_calendar.day_detail", ymd=tx.occurred_at.strftime("%Y-%m-%d")))

        focus = safe_str(request.form.get("focus") or "receipt_required", max_len=40)
        q = safe_str(request.form.get("q"), max_len=120)
        month_key = parse_date_ym(request.form.get("month")) or month_key_from_tx(tx)
        limit = parse_limit(request.form.get("limit"), default=200)

        item = db.session.query(EvidenceItem).filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        prev_evidence = {
            "exists": bool(item),
            "requirement": (item.requirement if item else None),
            "status": (item.status if item else None),
        }
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
            _set_review_undo(
                {
                    "kind": "evidence",
                    "tx_id": int(tx_id),
                    "prev": prev_evidence,
                },
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                action_type=("attach" if action == "attached" else "mark_unneeded"),
            )
            if action == "attached":
                flash("증빙 첨부 완료로 처리했어요. 목록에서 빠졌어요.", "success")
            elif action == "not_needed":
                flash("증빙 불필요로 처리했어요.", "success")
            else:
                flash("증빙 필요 상태로 되돌렸어요.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("저장 중 문제가 발생했어요.", "error")

        month_first = parse_month(month_key)
        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)
        next_id = next_review_tx_id(
            db=db,
            Transaction=Transaction,
            IncomeLabel=IncomeLabel,
            ExpenseLabel=ExpenseLabel,
            EvidenceItem=EvidenceItem,
            user_pk=user_pk,
            focus=focus,
            start_dt=start_dt,
            end_dt=end_dt,
            q=q,
            after_tx=tx,
        )

        return back_to_review(month_key, focus, q, limit=limit, anchor_tx_id=next_id)

    @bp.get("/review/evidence/<int:tx_id>/upload")
    def review_evidence_upload_page(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")

        focus = (request.args.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        if focus not in REVIEW_FOCUS:
            focus = DEFAULT_REVIEW_FOCUS

        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()

        partial = is_partial()
        tpl = "calendar/partials/receipt_wizard_upload.html" if partial else "calendar/evidence_upload.html"
        follow_up_answers = _load_follow_up_answers_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})
        reinforcement_data = _load_reinforcement_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})

        return render_template(
            tpl,
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            tx=tx,
            ev=ev,
            expense_guidance=build_receipt_expense_inline_guidance(
                tx=tx,
                focus_kind=focus,
                follow_up_answers=follow_up_answers,
                reinforcement_data=reinforcement_data,
            ),
            expense_followup_answers=follow_up_answers,
            expense_reinforcement=reinforcement_data,
            expense_guide_url=url_for("web_guide.expense_guide"),
        )

    @bp.post("/review/evidence/<int:tx_id>/upload")
    def review_evidence_upload(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")

        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        if focus not in REVIEW_FOCUS:
            focus = DEFAULT_REVIEW_FOCUS

        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev:
            ev = EvidenceItem(user_pk=user_pk, transaction_id=tx_id, requirement="maybe", status="missing", note=None)
            db.session.add(ev)
            db.session.flush()

        files = request.files.getlist("files")
        if not files:
            one = request.files.get("file")
            if one:
                files = [one]

        receipt_text = (request.form.get("receipt_text") or "").strip()
        receipt_type = (request.form.get("receipt_type") or "").strip()
        if not receipt_type:
            receipt_type = "electronic" if receipt_text else "paper"

        old_file_key = str(ev.file_key or "").strip()
        try:
            if files:
                stored = store_evidence_file_multi(user_pk=user_pk, tx_id=tx_id, month_key=month_key, files=files)
            else:
                stored = store_evidence_text_file(user_pk=user_pk, tx_id=tx_id, month_key=month_key, text=receipt_text)
        except Exception as e:
            _, friendly = normalize_receipt_error(f"업로드 실패: {e}")
            flash(friendly, "error")
            return redirect(
                url_for(
                    "web_calendar.review_evidence_upload_page",
                    tx_id=tx_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                    partial=("1" if is_partial() else None),
                )
            )

        ev.file_key = stored.file_key
        ev.original_filename = stored.original_filename
        ev.mime_type = stored.mime_type
        ev.size_bytes = int(stored.size_bytes)
        ev.sha256 = stored.sha256
        ev.uploaded_at = utcnow_fn()
        ev.retention_until = default_retention_until()
        ev.status = "attached"

        if ev.requirement == "not_needed":
            ev.requirement = "maybe"

        meta = {
            "receipt_type": receipt_type,
            "capture_mode": ("text" if (not files and receipt_text) else "file"),
        }
        ev.note = "receipt_meta:" + json.dumps(meta, ensure_ascii=False)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                if stored.file_key:
                    delete_physical_file(stored.file_key)
            except Exception:
                pass
            flash("업로드 저장 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
            return redirect(
                url_for(
                    "web_calendar.review_evidence_upload_page",
                    tx_id=tx_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                    partial=("1" if is_partial() else None),
                )
            )

        if old_file_key and old_file_key != str(stored.file_key or "").strip():
            try:
                delete_physical_file(old_file_key)
            except Exception:
                pass

        flash("영수증/증빙이 업로드되었습니다.", "ok")

        return redirect(
            url_for(
                "web_calendar.receipt_confirm_page",
                tx_id=tx_id,
                month=month_key,
                focus=focus,
                q=q,
                limit=limit,
                partial=("1" if is_partial() else None),
            )
        )

    @bp.get("/review/evidence/<int:tx_id>/file")
    def review_evidence_file(tx_id: int):
        user_pk = uid_getter()
        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev or not ev.file_key:
            abort(404)
        try:
            p = resolve_file_path(ev.file_key)
        except Exception:
            abort(404)
        if not p.exists():
            abort(404)
        return send_file(p, mimetype=ev.mime_type or "application/octet-stream")

    @bp.get("/review/evidence/<int:tx_id>/parse")
    def review_evidence_parse_page(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.args.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev or not ev.file_key:
            flash("먼저 증빙 파일을 업로드해주세요.", "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

        try:
            abs_path = resolve_file_path(ev.file_key)
        except Exception:
            flash("업로드 파일을 찾지 못했어요. 다시 업로드해 주세요.", "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")
        try:
            draft = parse_receipt_from_file(abs_path=abs_path, mime_type=ev.mime_type or "")
        except Exception as e:
            raw = str(e or "")
            _, friendly = normalize_receipt_error(raw)
            if "openai_api_key" in raw.lower():
                friendly = "영수증 분석 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요."
            flash(friendly, "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

        parsed = draft.parsed or {}
        view = {
            "merchant": parsed.get("merchant", ""),
            "paid_at": parsed.get("paid_at", ""),
            "total_krw": parsed.get("total_krw", ""),
            "vat_krw": parsed.get("vat_krw", ""),
            "payment_method": parsed.get("payment_method", ""),
            "card_tail": parsed.get("card_tail", ""),
            "approval_no": parsed.get("approval_no", ""),
            "items": parsed.get("items", []),
        }
        follow_up_answers = _load_follow_up_answers_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})
        reinforcement_data = _load_reinforcement_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})

        return render_template(
            "calendar/receipt_confirm.html",
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            tx=tx,
            ev=ev,
            draft_ok=draft.ok,
            draft_provider=draft.provider,
            draft_error=draft.error,
            view=view,
            expense_guidance=build_receipt_expense_inline_guidance(
                tx=tx,
                draft=view,
                focus_kind=focus,
                follow_up_answers=follow_up_answers,
                reinforcement_data=reinforcement_data,
            ),
            expense_followup_answers=follow_up_answers,
            expense_reinforcement=reinforcement_data,
            expense_guide_url=url_for("web_guide.expense_guide"),
        )

    @bp.post("/review/evidence/<int:tx_id>/parse")
    def review_evidence_parse_save(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev:
            abort(404)

        payload = {
            "merchant": (request.form.get("merchant") or "").strip(),
            "paid_at": (request.form.get("paid_at") or "").strip(),
            "total_krw": (request.form.get("total_krw") or "").strip(),
            "vat_krw": (request.form.get("vat_krw") or "").strip(),
            "payment_method": (request.form.get("payment_method") or "").strip(),
            "card_tail": (request.form.get("card_tail") or "").strip(),
            "approval_no": (request.form.get("approval_no") or "").strip(),
        }

        ev.note = "receipt_parse:" + json.dumps(payload, ensure_ascii=False)

        db.session.commit()
        flash("영수증 정보가 저장되었습니다.", "ok")

        return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

    @bp.get("/review/evidence/<int:tx_id>/confirm")
    def receipt_confirm_page(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.args.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev or not ev.file_key:
            flash("먼저 영수증/증빙을 업로드해주세요.", "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

        receipt_type = ""
        if ev.note and ev.note.startswith("receipt_meta:"):
            try:
                meta = json.loads(ev.note[len("receipt_meta:") :])
                receipt_type = str(meta.get("receipt_type") or "")
            except Exception:
                receipt_type = ""
        if not receipt_type:
            receipt_type = (request.args.get("receipt_type") or "").strip() or "paper"

        try:
            abs_path = resolve_file_path(ev.file_key)
        except Exception:
            flash("업로드 파일을 찾지 못했어요. 다시 업로드해 주세요.", "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

        try:
            if (ev.mime_type or "").startswith("text/") or abs_path.suffix.lower() in (".txt",):
                try:
                    txt = abs_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    txt = ""
                draft = parse_receipt_from_text(text=txt)
            else:
                draft = parse_receipt_from_file(abs_path=abs_path, mime_type=(ev.mime_type or ""))
        except Exception as e:
            raw = str(e or "")
            _, friendly = normalize_receipt_error(raw)
            if "openai_api_key" in raw.lower():
                friendly = "영수증 분석 설정이 아직 준비되지 않았어요(개발용 설정 필요). 잠시 후 다시 시도해주세요."
            flash(friendly, "error")
            return redirect(url_for("web_calendar.review", month=month_key, focus=focus, q=q, limit=limit) + f"#tx-{tx_id}")

        view = draft.parsed or {}
        follow_up_answers = _load_follow_up_answers_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})
        reinforcement_data = _load_reinforcement_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})

        partial = is_partial()
        tpl = "calendar/partials/receipt_wizard_confirm.html" if partial else "calendar/receipt_confirm.html"

        return render_template(
            tpl,
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            tx=tx,
            ev=ev,
            receipt_type=receipt_type,
            draft_ok=draft.ok,
            draft_provider=draft.provider,
            draft_error=draft.error,
            view=view,
            expense_guidance=build_receipt_expense_inline_guidance(
                tx=tx,
                draft=view,
                focus_kind=focus,
                receipt_type=receipt_type,
                follow_up_answers=follow_up_answers,
                reinforcement_data=reinforcement_data,
            ),
            expense_followup_answers=follow_up_answers,
            expense_reinforcement=reinforcement_data,
            expense_guide_url=url_for("web_guide.expense_guide"),
        )

    @bp.post("/review/evidence/<int:tx_id>/expense-followup")
    def review_expense_followup_save(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)
        return_view = safe_str(request.form.get("return_view") or "review", max_len=32).lower()
        if return_view not in {"review", "confirm", "match"}:
            return_view = "review"

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        stored_draft, stored_receipt_type = _parse_receipt_draft_from_evidence(ev)
        form_draft, form_receipt_type = _follow_up_context_from_form()
        draft = form_draft or stored_draft
        receipt_type = form_receipt_type or stored_receipt_type
        answers_payload = extract_follow_up_answers_from_form(request.form)
        before_est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        redirect_params = None

        try:
            result = save_receipt_follow_up_answers_and_re_evaluate(
                db.session,
                ReceiptExpenseFollowupAnswer,
                user_pk=int(user_pk),
                answered_by=int(user_pk),
                tx=tx,
                evidence_item=ev,
                answers_payload=answers_payload,
                draft=draft,
                focus_kind=focus,
                receipt_type=receipt_type,
            )
            db.session.commit()
            after_est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
            effect_level = str((result or {}).get("decision", {}).get("level") or "needs_review")
            redirect_params = _build_receipt_tax_effect_redirect_params(
                before_est=before_est,
                after_est=after_est,
                effect_level=effect_level,
            )
            active_metric = get_active_seasonal_card(session)
            if active_metric and str(active_metric.get("completion_action") or "") == "review_cleanup_saved":
                refreshed_experience = build_seasonal_experience(
                    user_pk=int(user_pk),
                    month_key=month_key,
                    urls={},
                )
                record_seasonal_card_event(
                    user_pk=int(user_pk),
                    event="seasonal_card_completed",
                    route="web_calendar.review_expense_followup_save",
                    season_focus=str(active_metric.get("season_focus") or ""),
                    card_type=str(active_metric.get("card_type") or ""),
                    cta_target=str(active_metric.get("cta_target") or ""),
                    source_screen=str(active_metric.get("source_screen") or "unknown"),
                    priority=int(active_metric.get("priority") or 0),
                    completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
                    completion_state_after=(
                        seasonal_card_completion_state(refreshed_experience, str(active_metric.get("card_type") or ""))
                        or str(active_metric.get("completion_state_before") or "todo")
                    ),
                    month_key=str(active_metric.get("month_key") or month_key),
                    target_ids=[int(tx_id)],
                    extra={"saved_kind": "follow_up"},
                )
                clear_active_seasonal_card(session)
        except ValueError as exc:
            db.session.rollback()
            message = "답변을 저장하지 못했어요. 질문을 다시 확인해 주세요."
            code = str(exc or "")
            if code == "missing_follow_up_answers":
                message = "답변을 하나 이상 입력해 주세요."
            elif code.startswith("invalid_answer_value:"):
                message = "질문 답변 형식이 올바르지 않아요."
            elif code.startswith("invalid_question_key:"):
                message = "질문 구성이 올바르지 않아요. 화면을 새로고침한 뒤 다시 시도해 주세요."
            elif code == "follow_up_storage_not_ready":
                message = "추가 질문 저장 기능을 준비하는 중이에요. 데이터베이스 업데이트 후 다시 시도해 주세요."
            elif code == "invalid_transaction":
                message = "대상 거래를 찾을 수 없어요."
            flash(message, "error")
        except Exception:
            db.session.rollback()
            flash("답변 저장 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")

        return _redirect_after_follow_up_save(
            tx_id=tx_id,
            month_key=month_key,
            focus=focus,
            q=q,
            limit=limit,
            return_view=return_view,
            extra_params=redirect_params,
        )

    @bp.post("/review/evidence/<int:tx_id>/expense-reinforcement")
    def review_expense_reinforcement_save(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)
        return_view = safe_str(request.form.get("return_view") or "review", max_len=32).lower()
        if return_view not in {"review", "confirm", "match"}:
            return_view = "review"

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        stored_draft, stored_receipt_type = _parse_receipt_draft_from_evidence(ev)
        form_draft, form_receipt_type = _follow_up_context_from_form()
        draft = form_draft or stored_draft
        receipt_type = form_receipt_type or stored_receipt_type
        reinforcement_payload = extract_reinforcement_payload_from_form(request.form)
        supporting_file = request.files.get("reinforce__supporting_file")
        before_est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        redirect_params = None

        try:
            result = save_receipt_reinforcement_and_re_evaluate(
                db.session,
                ReceiptExpenseReinforcement,
                ReceiptExpenseFollowupAnswer,
                user_pk=int(user_pk),
                updated_by=int(user_pk),
                tx=tx,
                evidence_item=ev,
                reinforcement_payload=reinforcement_payload,
                draft=draft,
                focus_kind=focus,
                receipt_type=receipt_type,
                month_key=month_key,
                supporting_file=supporting_file,
                store_supporting_file_fn=store_evidence_file_multi,
                delete_supporting_file_fn=delete_physical_file,
            )
            db.session.commit()
            after_est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
            effect_level = str((result or {}).get("decision", {}).get("level") or "needs_review")
            redirect_params = _build_receipt_tax_effect_redirect_params(
                before_est=before_est,
                after_est=after_est,
                effect_level=effect_level,
            )
            active_metric = get_active_seasonal_card(session)
            if active_metric and str(active_metric.get("completion_action") or "") == "review_cleanup_saved":
                refreshed_experience = build_seasonal_experience(
                    user_pk=int(user_pk),
                    month_key=month_key,
                    urls={},
                )
                record_seasonal_card_event(
                    user_pk=int(user_pk),
                    event="seasonal_card_completed",
                    route="web_calendar.review_expense_reinforcement_save",
                    season_focus=str(active_metric.get("season_focus") or ""),
                    card_type=str(active_metric.get("card_type") or ""),
                    cta_target=str(active_metric.get("cta_target") or ""),
                    source_screen=str(active_metric.get("source_screen") or "unknown"),
                    priority=int(active_metric.get("priority") or 0),
                    completion_state_before=str(active_metric.get("completion_state_before") or "todo"),
                    completion_state_after=(
                        seasonal_card_completion_state(refreshed_experience, str(active_metric.get("card_type") or ""))
                        or str(active_metric.get("completion_state_before") or "todo")
                    ),
                    month_key=str(active_metric.get("month_key") or month_key),
                    target_ids=[int(tx_id)],
                    extra={"saved_kind": "reinforcement"},
                )
                clear_active_seasonal_card(session)
        except ValueError as exc:
            db.session.rollback()
            code = str(exc or "")
            message = "보강 정보를 저장하지 못했어요. 내용을 다시 확인해 주세요."
            if code == "missing_reinforcement_payload":
                message = "보강할 내용을 하나 이상 입력하거나 파일을 첨부해 주세요."
            elif code.startswith("invalid_reinforcement_key:"):
                message = "보강 항목 구성이 올바르지 않아요. 화면을 새로고침한 뒤 다시 시도해 주세요."
            elif code.startswith("invalid_supporting_file:"):
                message = "보강 파일을 저장하지 못했어요. 파일 형식과 크기를 확인해 주세요."
            elif code == "reinforcement_storage_not_ready":
                message = "보강 정보 저장 기능을 준비하는 중이에요. 데이터베이스 업데이트 후 다시 시도해 주세요."
            elif code == "supporting_file_storage_unavailable":
                message = "보강 파일 저장 기능이 아직 준비되지 않았어요."
            elif code == "invalid_transaction":
                message = "대상 거래를 찾을 수 없어요."
            flash(message, "error")
        except Exception:
            db.session.rollback()
            flash("보강 정보 저장 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.", "error")

        return _redirect_after_follow_up_save(
            tx_id=tx_id,
            month_key=month_key,
            focus=focus,
            q=q,
            limit=limit,
            return_view=return_view,
            extra_params=redirect_params,
        )

    @bp.post("/review/evidence/<int:tx_id>/confirm")
    def receipt_confirm_save(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev:
            abort(404)

        receipt_type = (request.form.get("receipt_type") or "").strip() or "paper"

        payload = {
            "receipt_type": receipt_type,
            "merchant": (request.form.get("merchant") or "").strip(),
            "paid_at": (request.form.get("paid_at") or "").strip(),
            "total_krw": (request.form.get("total_krw") or "").strip(),
            "vat_krw": (request.form.get("vat_krw") or "").strip(),
            "payment_method": (request.form.get("payment_method") or "").strip(),
            "card_tail": (request.form.get("card_tail") or "").strip(),
            "approval_no": (request.form.get("approval_no") or "").strip(),
        }

        ev.note = "receipt_draft:" + json.dumps(payload, ensure_ascii=False)
        db.session.commit()

        return redirect(
            url_for(
                "web_calendar.receipt_match_page",
                tx_id=tx_id,
                month=month_key,
                focus=focus,
                q=q,
                limit=limit,
                partial=("1" if is_partial() else None),
            )
        )

    @bp.get("/review/evidence/<int:tx_id>/match")
    def receipt_match_page(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.args.get("month"))
        month_key = month_first.strftime("%Y-%m")
        focus = (request.args.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.args.get("q") or "").strip()
        limit = parse_limit(request.args.get("limit"), default=200)

        tx = Transaction.query.filter_by(user_pk=user_pk, id=tx_id).first()
        if not tx:
            abort(404)

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev:
            abort(404)

        quick_event = safe_str(request.args.get("quick_match_event"), max_len=32).lower()
        quick_candidate_tx_id = _parse_int(request.args.get("quick_candidate_tx_id"))
        if quick_event == "rejected":
            event_key = f"{month_key}:{int(tx_id)}:{int(quick_candidate_tx_id or 0)}:rejected"
            if str(session.get("quick_match_reject_key") or "") != event_key:
                _log_quick_match_metric(
                    user_pk=user_pk,
                    db=db,
                    ActionLog=ActionLog,
                    event="quick_match_rejected",
                    month_key=month_key,
                    tx_id=int(tx_id),
                    candidate_tx_id=int(quick_candidate_tx_id or 0),
                )
                session["quick_match_reject_key"] = event_key
                session.modified = True

        draft: dict = {}
        receipt_type = ""
        if ev.note and ev.note.startswith("receipt_draft:"):
            try:
                draft = json.loads(ev.note[len("receipt_draft:") :])
            except Exception:
                draft = {}
        if isinstance(draft, dict):
            receipt_type = str(draft.get("receipt_type") or "")

        total = _parse_int(str(draft.get("total_krw") or ""))
        merchant = str(draft.get("merchant") or "")
        paid_at_raw = str(draft.get("paid_at") or "").strip()
        receipt_last4 = ""
        card_tail_digits = re.sub(r"[^0-9]", "", str(draft.get("card_tail") or ""))
        if len(card_tail_digits) >= 4:
            receipt_last4 = card_tail_digits[-4:]

        paid_at_dt = _parse_receipt_paid_at(paid_at_raw)

        start_d, end_d = month_range(month_first)
        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.min)

        # 월 경계 근처 영수증(예: 3/1 결제, 카드 승인 2/28)까지 후보를 잡기 위해
        # 결제일이 있으면 ±7일 범위로 탐색하고, 없으면 기존 월 범위를 유지한다.
        search_start = start_dt
        search_end = end_dt
        if paid_at_dt:
            search_start = datetime.combine((paid_at_dt - timedelta(days=7)).date(), time.min)
            search_end = datetime.combine((paid_at_dt + timedelta(days=8)).date(), time.min)

        base = (
            db.session.query(Transaction)
            .filter(Transaction.user_pk == user_pk)
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= search_start, Transaction.occurred_at < search_end)
        )

        rows = base.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).limit(300).all()

        scored = _score_receipt_candidates(
            rows=rows,
            total=total,
            merchant=merchant,
            paid_at_dt=paid_at_dt,
            current_tx_id=int(tx.id),
        )
        top_scored = scored[:7]
        candidates = [row["tx"] for row in top_scored]
        candidate_meta = {
            int(row["tx"].id): {
                "score": float(row["score"]),
                "reason": " · ".join(row["reasons"][:3]) if row["reasons"] else "",
            }
            for row in top_scored
        }
        default_tx_id = tx.id
        if top_scored and float(top_scored[0]["score"]) >= 0.8:
            default_tx_id = int(top_scored[0]["tx"].id)

        account_options = list_accounts_for_ui(user_pk)
        recommended_account_id = None
        recommended_account_reason = ""
        default_tx_row = Transaction.query.filter_by(user_pk=user_pk, id=int(default_tx_id)).first()
        if default_tx_row and getattr(default_tx_row, "bank_account_id", None):
            recommended_account_id = int(default_tx_row.bank_account_id)
            recommended_account_reason = "매칭 거래 계좌를 추천했어요."
        elif receipt_last4:
            last4_matches = [x for x in account_options if str(x.get("last4") or "") == receipt_last4]
            if len(last4_matches) == 1:
                recommended_account_id = int(last4_matches[0]["id"])
                recommended_account_reason = f"영수증 끝자리 {receipt_last4}와 일치하는 계좌예요."
            elif len(last4_matches) > 1:
                recommended_account_reason = f"끝자리 {receipt_last4} 계좌가 여러 개라 직접 선택해 주세요."

        duplicate_sha_matches: list[dict] = []
        duplicate_sha_count = 0
        if (ev.sha256 or "").strip():
            dup_rows = (
                db.session.query(Transaction.id, Transaction.occurred_at, Transaction.counterparty, Transaction.amount_krw)
                .join(
                    EvidenceItem,
                    and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == user_pk),
                )
                .filter(Transaction.user_pk == user_pk)
                .filter(EvidenceItem.sha256 == ev.sha256)
                .filter(EvidenceItem.deleted_at.is_(None))
                .filter(Transaction.id != tx.id)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                .limit(3)
                .all()
            )
            duplicate_sha_count = int(len(dup_rows))
            duplicate_sha_matches = [
                {
                    "tx_id": int(r[0]),
                    "occurred_at": r[1],
                    "counterparty": r[2] or "알 수 없음",
                    "amount_krw": int(r[3] or 0),
                }
                for r in dup_rows
            ]

        est = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        basis = int(total or tx.amount_krw or 0)
        try:
            approx_saving = int(min(basis, int(est.estimated_profit_krw)) * float(est.tax_rate))
        except Exception:
            approx_saving = 0

        partial = is_partial()
        tpl = "calendar/partials/receipt_wizard_match.html" if partial else "calendar/receipt_match.html"
        follow_up_answers = _load_follow_up_answers_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})
        reinforcement_data = _load_reinforcement_for_tx_ids(user_pk, [tx_id]).get(int(tx_id), {})

        return render_template(
            tpl,
            month_key=month_key,
            month_first=month_first,
            focus=focus,
            q=q,
            limit=limit,
            tx=tx,
            ev=ev,
            draft=draft,
            receipt_type=receipt_type,
            candidates=candidates,
            default_tx_id=default_tx_id,
            candidate_meta=candidate_meta,
            duplicate_sha_count=duplicate_sha_count,
            duplicate_sha_matches=duplicate_sha_matches,
            approx_saving=approx_saving,
            tax_rate=float(est.tax_rate),
            account_options=account_options,
            recommended_account_id=recommended_account_id,
            recommended_account_reason=recommended_account_reason,
            receipt_last4=receipt_last4,
            expense_guidance=build_receipt_expense_inline_guidance(
                tx=tx,
                draft=draft,
                focus_kind=focus,
                receipt_type=receipt_type,
                follow_up_answers=follow_up_answers,
                reinforcement_data=reinforcement_data,
            ),
            expense_followup_answers=follow_up_answers,
            expense_reinforcement=reinforcement_data,
            expense_guide_url=url_for("web_guide.expense_guide"),
        )

    @bp.post("/review/evidence/<int:tx_id>/match")
    def receipt_match_save(tx_id: int):
        user_pk = uid_getter()

        month_first = parse_month(request.form.get("month"))
        month_key = month_first.strftime("%Y-%m")

        before = _compute_monthly_tax_estimate(user_pk, month_key=month_key)

        focus = (request.form.get("focus") or DEFAULT_REVIEW_FOCUS).strip()
        q = (request.form.get("q") or "").strip()
        limit = parse_limit(request.form.get("limit"), default=200)
        quick_event = safe_str(request.form.get("quick_match_event"), max_len=32).lower()
        quick_candidate_tx_id = _parse_int(request.form.get("quick_match_candidate_tx_id"))

        ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not ev:
            abort(404)

        original_tx_id = int(tx_id)
        original_source_prev = {
            "exists": True,
            "requirement": (ev.requirement if ev else None),
            "status": (ev.status if ev else None),
        }

        chosen_tx_id = int(request.form.get("chosen_tx_id") or tx_id)
        selected_bank_account_raw = safe_str(request.form.get("bank_account_id"), max_len=32)
        expense_kind = (request.form.get("expense_kind") or "unknown").strip()
        if expense_kind not in ("business", "personal", "mixed"):
            expense_kind = "mixed"

        replaced_target_file_key = ""
        target_prev_expense: dict[str, Any] | None = None
        target_prev_evidence: dict[str, Any] | None = {
            "exists": True,
            "requirement": (ev.requirement if ev else None),
            "status": (ev.status if ev else None),
        }

        if chosen_tx_id != tx_id:
            target_tx = Transaction.query.filter_by(user_pk=user_pk, id=chosen_tx_id).first()
            if not target_tx:
                flash("선택한 결제 내역을 찾을 수 없어요.", "error")
                return redirect(
                    url_for(
                        "web_calendar.receipt_match_page",
                        tx_id=tx_id,
                        month=month_key,
                        focus=focus,
                        q=q,
                        limit=limit,
                        partial=("1" if is_partial() else None),
                    )
                )

            target_ev = EvidenceItem.query.filter_by(user_pk=user_pk, transaction_id=chosen_tx_id).first()
            if not target_ev:
                target_ev = EvidenceItem(user_pk=user_pk, transaction_id=chosen_tx_id, requirement="maybe", status="missing", note=None)
                db.session.add(target_ev)
                db.session.flush()

            target_prev_evidence = {
                "exists": bool(target_ev),
                "requirement": (target_ev.requirement if target_ev else None),
                "status": (target_ev.status if target_ev else None),
            }

            replaced_target_file_key = str(target_ev.file_key or "").strip()
            if replaced_target_file_key == str(ev.file_key or "").strip():
                replaced_target_file_key = ""

            target_ev.file_key = ev.file_key
            target_ev.original_filename = ev.original_filename
            target_ev.mime_type = ev.mime_type
            target_ev.size_bytes = ev.size_bytes
            target_ev.sha256 = ev.sha256
            target_ev.uploaded_at = ev.uploaded_at
            target_ev.retention_until = ev.retention_until
            target_ev.deleted_at = None
            target_ev.note = ev.note

            ev.file_key = None
            ev.original_filename = None
            ev.mime_type = None
            ev.size_bytes = None
            ev.sha256 = None
            ev.uploaded_at = None
            ev.deleted_at = None
            ev.status = "missing"

            ev = target_ev
            tx_id = chosen_tx_id
        else:
            target_prev_evidence = {
                "exists": bool(ev),
                "requirement": (ev.requirement if ev else None),
                "status": (ev.status if ev else None),
            }

        prev_label_row = ExpenseLabel.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        target_prev_expense = {
            "label_exists": bool(prev_label_row),
            "label_status": (prev_label_row.status if prev_label_row else None),
            "label_confidence": int(prev_label_row.confidence or 0) if prev_label_row else 0,
            "label_by": (prev_label_row.labeled_by if prev_label_row else None),
            "evidence_exists": bool(target_prev_evidence and target_prev_evidence.get("exists")),
            "evidence_requirement": (target_prev_evidence.get("requirement") if target_prev_evidence else None),
            "evidence_status": (target_prev_evidence.get("status") if target_prev_evidence else None),
        }

        if expense_kind == "business":
            ev.requirement = "required"
            ev.status = "attached"
        elif expense_kind == "personal":
            ev.requirement = "not_needed"
            ev.status = "not_needed"
        else:
            ev.requirement = "maybe"
            ev.status = "attached"

        row = ExpenseLabel.query.filter_by(user_pk=user_pk, transaction_id=tx_id).first()
        if not row:
            row = ExpenseLabel(user_pk=user_pk, transaction_id=tx_id)
            db.session.add(row)

        row.status = expense_kind
        row.confidence = 100
        row.labeled_by = "auto"

        if selected_bank_account_raw:
            try:
                selected_bank_account_id = int(selected_bank_account_raw)
            except Exception:
                selected_bank_account_id = 0
            if selected_bank_account_id > 0:
                selected_account = (
                    UserBankAccount.query.filter(UserBankAccount.user_pk == int(user_pk))
                    .filter(UserBankAccount.id == int(selected_bank_account_id))
                    .first()
                )
                if selected_account:
                    target_tx = Transaction.query.filter_by(user_pk=user_pk, id=int(tx_id)).first()
                    if target_tx:
                        target_tx.bank_account_id = int(selected_account.id)
                        db.session.add(target_tx)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("영수증 반영 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.", "error")
            return redirect(
                url_for(
                    "web_calendar.receipt_match_page",
                    tx_id=tx_id,
                    month=month_key,
                    focus=focus,
                    q=q,
                    limit=limit,
                    partial=("1" if is_partial() else None),
                )
            )

        undo_payloads: list[dict[str, Any]] = []
        if isinstance(target_prev_expense, dict):
            undo_payloads.append(
                {
                    "kind": "expense",
                    "tx_id": int(tx_id),
                    "prev": target_prev_expense,
                }
            )
        if int(original_tx_id) != int(tx_id):
            undo_payloads.append(
                {
                    "kind": "evidence",
                    "tx_id": int(original_tx_id),
                    "prev": original_source_prev,
                }
            )
        if undo_payloads:
            _set_review_undo_many(
                undo_payloads,
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                action_type="attach",
            )

        if quick_event == "confirmed":
            _log_quick_match_metric(
                user_pk=user_pk,
                db=db,
                ActionLog=ActionLog,
                event="quick_match_confirmed",
                month_key=month_key,
                tx_id=int(original_tx_id),
                candidate_tx_id=int(quick_candidate_tx_id or tx_id),
            )

        if replaced_target_file_key:
            try:
                delete_physical_file(replaced_target_file_key)
            except Exception:
                pass

        after = _compute_monthly_tax_estimate(user_pk, month_key=month_key)
        effect_level = {
            "business": "high_likelihood",
            "mixed": "needs_review",
            "personal": "do_not_auto_allow",
        }.get(str(expense_kind or "").strip().lower(), "needs_review")
        redirect_url = (
            url_for(
                "web_calendar.review",
                month=month_key,
                focus=focus,
                q=q,
                limit=limit,
                **_build_receipt_tax_effect_redirect_params(
                    before_est=before,
                    after_est=after,
                    effect_level=effect_level,
                ),
            )
            + f"#tx-{tx_id}"
        )

        if is_partial():
            return jsonify({"ok": True, "redirect": redirect_url})

        return redirect(redirect_url)
