from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, func

from core.extensions import db
from domain.models import (
    EvidenceItem,
    ExpenseLabel,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    Transaction,
)
from services.onboarding import tax_profile_completion_meta, tax_profile_summary
from services.plan import can_download_package
from services.risk import compute_tax_estimate
from services.tax_package import build_tax_package_preview

MAY_FILING_FOCUS = "may_filing_focus"
NOVEMBER_PREPAYMENT_FOCUS = "november_prepayment_focus"
OFF_SEASON = "off_season"

SEASONAL_CARD_PENDING_SESSION_KEY = "seasonal_card_pending"
SEASONAL_CARD_ACTIVE_SESSION_KEY = "seasonal_card_active"
SEASONAL_CARD_LAND_QUERY_FLAG = "seasonal_card_land"
SEASONAL_CARD_TRACK_QUERY_KEYS = (
    SEASONAL_CARD_LAND_QUERY_FLAG,
    "seasonal_focus",
    "seasonal_card_type",
    "seasonal_cta_target",
    "seasonal_source_screen",
    "seasonal_priority",
    "seasonal_completion_state_before",
    "seasonal_month_key",
    "seasonal_completion_action",
)

SEASONAL_WINDOWS = {
    MAY_FILING_FOCUS: {"months": (4, 5, 6), "label": "5월 신고 시즌"},
    NOVEMBER_PREPAYMENT_FOCUS: {"months": (10, 11), "label": "11월 중간예납 시즌"},
    OFF_SEASON: {"months": (1, 2, 3, 7, 8, 9, 12), "label": "비시즌"},
}

SEASONAL_SCREEN_CARD_TYPES = {
    "review": {
        MAY_FILING_FOCUS: ("may_receipt_cleanup",),
        NOVEMBER_PREPAYMENT_FOCUS: ("november_receipt_reinforce",),
        OFF_SEASON: ("offseason_monthly_review", "offseason_receipt_cleanup"),
    },
    "tax_buffer": {
        MAY_FILING_FOCUS: ("may_accuracy",),
        NOVEMBER_PREPAYMENT_FOCUS: ("november_halfyear_check", "november_buffer_check"),
        OFF_SEASON: ("offseason_accuracy",),
    },
    "package": {
        MAY_FILING_FOCUS: ("may_package_ready",),
        NOVEMBER_PREPAYMENT_FOCUS: ("november_package_ready",),
        OFF_SEASON: ("offseason_package_ready",),
    },
}

SEASONAL_CARD_COMPLETION_ACTIONS = {
    "may_accuracy": "tax_profile_saved",
    "may_receipt_cleanup": "review_cleanup_saved",
    "may_package_ready": "package_downloaded",
    "november_halfyear_check": "tax_profile_saved",
    "november_receipt_reinforce": "review_cleanup_saved",
    "november_buffer_check": "tax_buffer_adjusted",
    "offseason_monthly_review": "review_cleanup_saved",
    "offseason_accuracy": "tax_profile_saved",
    "offseason_package_ready": "package_downloaded",
}

SEASONAL_UX_ALLOWED_INFERENCE_SIGNALS = (
    "receipt_pending_count",
    "reinforcement_pending_count",
    "tax_accuracy_gap",
    "package_ready",
    "receipt_pending_expense_krw",
)

SEASONAL_UX_FORBIDDEN_INFERENCE_SIGNALS = (
    "guessed_withholding_from_patterns",
    "guessed_vat_type",
    "guessed_prepaid_tax_level",
)


def determine_season_focus(today: date | None = None) -> str:
    ref = today or date.today()
    if ref.month in SEASONAL_WINDOWS[MAY_FILING_FOCUS]["months"]:
        return MAY_FILING_FOCUS
    if ref.month in SEASONAL_WINDOWS[NOVEMBER_PREPAYMENT_FOCUS]["months"]:
        return NOVEMBER_PREPAYMENT_FOCUS
    return OFF_SEASON


def _month_start(month_key: str | None, *, today: date | None = None) -> date:
    ref = today or date.today()
    raw = str(month_key or "").strip()
    if len(raw) == 7 and raw[4] == "-":
        try:
            year = int(raw[:4])
            month = int(raw[5:7])
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return date(year, month, 1)
        except Exception:
            pass
    return date(ref.year, ref.month, 1)


def _month_range(month_key: str | None, *, today: date | None = None) -> tuple[datetime, datetime, str]:
    start_date = _month_start(month_key, today=today)
    if start_date.month == 12:
        end_date = date(start_date.year + 1, 1, 1)
    else:
        end_date = date(start_date.year, start_date.month + 1, 1)
    return (
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date, datetime.min.time()),
        start_date.strftime("%Y-%m"),
    )


def _completion_state(*, done: bool, partial: bool = False) -> str:
    if done:
        return "done"
    if partial:
        return "in_progress"
    return "todo"


def _priority_rank(card: dict[str, Any]) -> tuple[int, int]:
    state_order = {"todo": 0, "in_progress": 1, "done": 2}
    priority = card.get("priority_effective", card.get("priority", 99))
    try:
        priority_value = int(priority)
    except Exception:
        priority_value = 99
    try:
        adjustment_score = int(card.get("priority_adjustment_score") or 0)
    except Exception:
        adjustment_score = 0
    base_priority = card.get("priority_base", card.get("priority", 99))
    try:
        base_priority_value = int(base_priority)
    except Exception:
        base_priority_value = 99
    return (
        state_order.get(str(card.get("completion_state") or "todo"), 9),
        priority_value,
        -adjustment_score,
        base_priority_value,
    )


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


def _seasonal_card_priority_adjustment(card_type: str, facts: dict[str, Any]) -> dict[str, Any]:
    normalized_type = str(card_type or "").strip().lower()
    receipt_pending_count = _safe_int(facts.get("receipt_pending_count"))
    reinforcement_pending_count = _safe_int(facts.get("reinforcement_pending_count"))
    pending_expense_krw = _safe_int(facts.get("receipt_pending_expense_krw"))
    tax_accuracy_gap = bool(facts.get("tax_accuracy_gap"))
    package_ready = bool(facts.get("package_ready"))
    can_download_package_flag = bool(facts.get("can_download_package"))
    pending_total = receipt_pending_count + reinforcement_pending_count

    score = 0
    reasons: list[str] = []

    if normalized_type in {"may_receipt_cleanup", "november_receipt_reinforce", "offseason_monthly_review"}:
        if pending_total >= 3:
            score += 1
            reasons.append("pending_backlog_present")
        if reinforcement_pending_count > 0:
            score += 1
            reasons.append("reinforcement_pending_present")
        if pending_expense_krw > 0:
            score += 1
            reasons.append("pending_expense_present")
    elif normalized_type in {"may_accuracy", "november_halfyear_check", "offseason_accuracy"}:
        if tax_accuracy_gap:
            score += 1
            reasons.append("tax_accuracy_gap")
    elif normalized_type in {"may_package_ready", "november_package_ready", "offseason_package_ready"}:
        if package_ready and pending_total == 0 and can_download_package_flag:
            score += 1
            reasons.append("package_ready_with_low_pending")

    applied = score > 0
    return {
        "priority_adjustment_score": score,
        "priority_adjustment_reasons": reasons,
        "priority_adjustment_reason": ", ".join(reasons),
        "priority_adjustment_applied": applied,
    }


def _apply_seasonal_priority_inference(cards: list[dict[str, Any]], facts: dict[str, Any]) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    for card in cards:
        item = dict(card)
        base_priority = _safe_int(item.get("priority"), 99)
        adjustment = _seasonal_card_priority_adjustment(str(item.get("card_type") or ""), facts)
        effective_priority = base_priority
        if int(adjustment.get("priority_adjustment_score") or 0) > 0:
            effective_priority = max(0, base_priority - 1)
        item["priority_base"] = base_priority
        item["priority_effective"] = effective_priority
        item["priority"] = effective_priority
        item.update(adjustment)
        adjusted.append(item)
    return adjusted


def _cta_target_for_card(*, profile_target: bool, default_target: str) -> str:
    return "profile" if profile_target else str(default_target or "unknown")


def _append_query_params(url: str, params: dict[str, Any] | None) -> str:
    base = str(url or "").strip()
    if not base:
        return base
    query_updates = {str(k): str(v) for k, v in dict(params or {}).items() if str(v).strip() != ""}
    if not query_updates:
        return base
    try:
        parts = urlsplit(base)
        query_pairs = dict(parse_qsl(parts.query, keep_blank_values=True))
        query_pairs.update(query_updates)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_pairs), parts.fragment))
    except Exception:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{urlencode(query_updates)}"


def seasonal_card_completion_action(card_type: str) -> str:
    return str(SEASONAL_CARD_COMPLETION_ACTIONS.get(str(card_type or "").strip(), "") or "")


def normalize_seasonal_metric_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    completion_before = str(raw.get("completion_state_before") or "").strip().lower()
    completion_after = str(raw.get("completion_state_after") or "").strip().lower()
    if completion_before not in {"todo", "in_progress", "done"}:
        completion_before = "todo"
    if completion_after not in {"todo", "in_progress", "done"}:
        completion_after = completion_before
    month_key = str(raw.get("month_key") or "").strip()
    if len(month_key) != 7 or month_key[4] != "-":
        month_key = ""
    return {
        "season_focus": str(raw.get("season_focus") or OFF_SEASON).strip().lower() or OFF_SEASON,
        "card_type": str(raw.get("card_type") or "unknown").strip().lower() or "unknown",
        "cta_target": str(raw.get("cta_target") or "unknown").strip().lower() or "unknown",
        "source_screen": str(raw.get("source_screen") or "unknown").strip().lower() or "unknown",
        "priority": int(raw.get("priority") or 0),
        "completion_state_before": completion_before,
        "completion_state_after": completion_after,
        "month_key": month_key,
        "completion_action": str(raw.get("completion_action") or "").strip().lower(),
    }


def build_seasonal_metric_payload(
    card: dict[str, Any] | None,
    *,
    season_focus: str,
    source_screen: str,
    month_key: str | None,
) -> dict[str, Any]:
    raw = dict(card or {})
    return normalize_seasonal_metric_payload(
        {
            "season_focus": season_focus,
            "card_type": raw.get("card_type"),
            "cta_target": raw.get("cta_target"),
            "source_screen": source_screen,
            "priority": raw.get("priority_effective", raw.get("priority")),
            "completion_state_before": raw.get("completion_state"),
            "month_key": month_key,
            "completion_action": raw.get("completion_action") or seasonal_card_completion_action(str(raw.get("card_type") or "")),
        }
    )


def build_seasonal_tracking_query_params(metric_payload: dict[str, Any], *, redirect_to: str) -> dict[str, str]:
    payload = normalize_seasonal_metric_payload(metric_payload)
    return {
        "redirect_to": str(redirect_to or "").strip(),
        "season_focus": str(payload.get("season_focus") or OFF_SEASON),
        "seasonal_card_type": str(payload.get("card_type") or "unknown"),
        "seasonal_cta_target": str(payload.get("cta_target") or "unknown"),
        "seasonal_source_screen": str(payload.get("source_screen") or "unknown"),
        "seasonal_priority": str(int(payload.get("priority") or 0)),
        "seasonal_completion_state_before": str(payload.get("completion_state_before") or "todo"),
        "seasonal_month_key": str(payload.get("month_key") or ""),
        "seasonal_completion_action": str(payload.get("completion_action") or ""),
    }


def seasonal_metric_payload_from_landing_args(args: Any) -> dict[str, Any] | None:
    if str(getattr(args, "get", lambda *_args, **_kwargs: "")(SEASONAL_CARD_LAND_QUERY_FLAG) or "").strip() != "1":
        return None
    return normalize_seasonal_metric_payload(
        {
            "season_focus": getattr(args, "get", lambda *_args, **_kwargs: "")("season_focus"),
            "card_type": getattr(args, "get", lambda *_args, **_kwargs: "")("seasonal_card_type"),
            "cta_target": getattr(args, "get", lambda *_args, **_kwargs: "")("seasonal_cta_target"),
            "source_screen": getattr(args, "get", lambda *_args, **_kwargs: "")("seasonal_source_screen"),
            "priority": getattr(args, "get", lambda *_args, **_kwargs: 0)("seasonal_priority"),
            "completion_state_before": getattr(args, "get", lambda *_args, **_kwargs: "todo")("seasonal_completion_state_before"),
            "month_key": getattr(args, "get", lambda *_args, **_kwargs: "")("seasonal_month_key"),
            "completion_action": getattr(args, "get", lambda *_args, **_kwargs: "")("seasonal_completion_action"),
        }
    )


def append_seasonal_landing_params(url: str, metric_payload: dict[str, Any]) -> str:
    payload = normalize_seasonal_metric_payload(metric_payload)
    return _append_query_params(
        url,
        {
            SEASONAL_CARD_LAND_QUERY_FLAG: "1",
            "season_focus": payload.get("season_focus"),
            "seasonal_card_type": payload.get("card_type"),
            "seasonal_cta_target": payload.get("cta_target"),
            "seasonal_source_screen": payload.get("source_screen"),
            "seasonal_priority": payload.get("priority"),
            "seasonal_completion_state_before": payload.get("completion_state_before"),
            "seasonal_month_key": payload.get("month_key"),
            "seasonal_completion_action": payload.get("completion_action"),
        },
    )


def store_pending_seasonal_card(session_obj: Any, metric_payload: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_seasonal_metric_payload(metric_payload)
    session_obj[SEASONAL_CARD_PENDING_SESSION_KEY] = dict(payload)
    try:
        session_obj.modified = True
    except Exception:
        pass
    return payload


def clear_pending_seasonal_card(session_obj: Any) -> None:
    session_obj.pop(SEASONAL_CARD_PENDING_SESSION_KEY, None)
    try:
        session_obj.modified = True
    except Exception:
        pass


def activate_pending_seasonal_card(session_obj: Any) -> dict[str, Any] | None:
    pending = session_obj.pop(SEASONAL_CARD_PENDING_SESSION_KEY, None)
    payload = normalize_seasonal_metric_payload(pending)
    if not payload.get("card_type"):
        return None
    session_obj[SEASONAL_CARD_ACTIVE_SESSION_KEY] = dict(payload)
    try:
        session_obj.modified = True
    except Exception:
        pass
    return payload


def set_active_seasonal_card(session_obj: Any, metric_payload: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_seasonal_metric_payload(metric_payload)
    session_obj[SEASONAL_CARD_ACTIVE_SESSION_KEY] = dict(payload)
    try:
        session_obj.modified = True
    except Exception:
        pass
    return payload


def get_active_seasonal_card(session_obj: Any) -> dict[str, Any] | None:
    active = session_obj.get(SEASONAL_CARD_ACTIVE_SESSION_KEY)
    if not active:
        return None
    return normalize_seasonal_metric_payload(active)


def clear_active_seasonal_card(session_obj: Any) -> None:
    session_obj.pop(SEASONAL_CARD_ACTIVE_SESSION_KEY, None)
    try:
        session_obj.modified = True
    except Exception:
        pass


def find_seasonal_card(seasonal_experience: dict[str, Any] | None, card_type: str) -> dict[str, Any] | None:
    if not isinstance(seasonal_experience, dict):
        return None
    target_type = str(card_type or "").strip().lower()
    for card in seasonal_experience.get("cards") or []:
        if not isinstance(card, dict):
            continue
        if str(card.get("card_type") or "").strip().lower() == target_type:
            return card
    return None


def seasonal_card_completion_state(seasonal_experience: dict[str, Any] | None, card_type: str) -> str | None:
    card = find_seasonal_card(seasonal_experience, card_type)
    if not isinstance(card, dict):
        return None
    state = str(card.get("completion_state") or "").strip().lower()
    return state or None


def decorate_seasonal_cards_for_tracking(
    seasonal_experience: dict[str, Any] | None,
    *,
    source_screen: str,
    month_key: str | None,
    click_url_builder,
) -> dict[str, Any] | None:
    if not isinstance(seasonal_experience, dict):
        return seasonal_experience
    season_focus = str(seasonal_experience.get("season_focus") or OFF_SEASON)
    for card in seasonal_experience.get("cards") or []:
        if not isinstance(card, dict):
            continue
        metric_payload = build_seasonal_metric_payload(
            card,
            season_focus=season_focus,
            source_screen=source_screen,
            month_key=month_key,
        )
        card["metric_payload"] = metric_payload
        card["metric_cta_url"] = click_url_builder(metric_payload, str(card.get("cta_url") or ""))
    return seasonal_experience


def decorate_seasonal_context_for_tracking(
    seasonal_context: dict[str, Any] | None,
    *,
    month_key: str | None,
    click_url_builder,
) -> dict[str, Any] | None:
    if not isinstance(seasonal_context, dict):
        return seasonal_context
    metric_payload = build_seasonal_metric_payload(
        seasonal_context,
        season_focus=str(seasonal_context.get("season_focus") or OFF_SEASON),
        source_screen=str(seasonal_context.get("source_screen") or "unknown"),
        month_key=month_key,
    )
    seasonal_context["metric_payload"] = metric_payload
    seasonal_context["metric_cta_url"] = click_url_builder(metric_payload, str(seasonal_context.get("cta_url") or ""))
    return seasonal_context


def collect_seasonal_user_state(user_pk: int, month_key: str | None = None, *, today: date | None = None) -> dict[str, Any]:
    start_dt, end_dt, normalized_month_key = _month_range(month_key, today=today)
    profile_meta = tax_profile_completion_meta(int(user_pk))
    profile_summary = tax_profile_summary(int(user_pk))

    has_transactions = bool(
        (
            db.session.query(func.count(Transaction.id))
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .scalar()
        )
        or 0
    )

    receipt_required_count = int(
        (
            db.session.query(func.count(func.distinct(Transaction.id)))
            .select_from(Transaction)
            .join(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == int(user_pk)),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(EvidenceItem.requirement == "required")
            .filter(EvidenceItem.status == "missing")
            .scalar()
        )
        or 0
    )
    receipt_attach_count = int(
        (
            db.session.query(func.count(func.distinct(Transaction.id)))
            .select_from(Transaction)
            .join(
                EvidenceItem,
                and_(EvidenceItem.transaction_id == Transaction.id, EvidenceItem.user_pk == int(user_pk)),
            )
            .filter(Transaction.user_pk == int(user_pk))
            .filter(Transaction.direction == "out")
            .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
            .filter(EvidenceItem.requirement == "maybe")
            .filter(EvidenceItem.status == "missing")
            .scalar()
        )
        or 0
    )
    expense_confirm_count = int(
        (
            db.session.query(func.count(func.distinct(Transaction.id)))
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
            .filter((ExpenseLabel.id.is_(None)) | (ExpenseLabel.status.in_(("unknown", "mixed"))))
            .filter((EvidenceItem.status == "attached") | (EvidenceItem.file_key.isnot(None)))
            .scalar()
        )
        or 0
    )
    try:
        reinforcement_pending_count = int(
            (
                db.session.query(func.count(func.distinct(ReceiptExpenseFollowupAnswer.transaction_id)))
                .select_from(ReceiptExpenseFollowupAnswer)
                .join(Transaction, Transaction.id == ReceiptExpenseFollowupAnswer.transaction_id)
                .outerjoin(
                    ReceiptExpenseReinforcement,
                    and_(
                        ReceiptExpenseReinforcement.user_pk == ReceiptExpenseFollowupAnswer.user_pk,
                        ReceiptExpenseReinforcement.transaction_id == ReceiptExpenseFollowupAnswer.transaction_id,
                    ),
                )
                .filter(ReceiptExpenseFollowupAnswer.user_pk == int(user_pk))
                .filter(Transaction.direction == "out")
                .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
                .filter(ReceiptExpenseReinforcement.id.is_(None))
                .scalar()
            )
            or 0
        )
    except Exception:
        reinforcement_pending_count = 0

    preview = {}
    preflight = {}
    try:
        preview = build_tax_package_preview(user_pk=int(user_pk), month_key=normalized_month_key) or {}
        preflight = dict(preview.get("preflight") or {})
    except Exception:
        preflight = {}

    tax_est = compute_tax_estimate(
        user_pk=int(user_pk),
        month_key=normalized_month_key,
        prefer_monthly_signal=True,
    )
    withheld_input_missing = profile_summary.get("withheld_tax_annual_krw") is None
    prepaid_input_missing = profile_summary.get("prepaid_tax_annual_krw") is None
    withholding_known = str(profile_summary.get("withholding_3_3") or "unknown") != "unknown"
    income_class_known = str(profile_summary.get("income_classification") or "unknown") != "unknown"
    tax_accuracy_gap = bool(
        (not bool(profile_meta.get("is_complete")))
        or (not withholding_known)
        or (not income_class_known)
        or withheld_input_missing
        or prepaid_input_missing
    )

    return {
        "month_key": normalized_month_key,
        "has_transactions": bool(has_transactions),
        "profile_completion_percent": int(profile_meta.get("percent") or 0),
        "profile_complete": bool(profile_meta.get("is_complete")),
        "tax_accuracy_gap": bool(tax_accuracy_gap),
        "paid_tax_inputs_missing": bool(withheld_input_missing or prepaid_input_missing),
        "withholding_info_missing": bool(not withholding_known),
        "receipt_required_count": int(receipt_required_count),
        "receipt_attach_count": int(receipt_attach_count),
        "expense_confirm_count": int(expense_confirm_count),
        "receipt_pending_count": int(receipt_required_count + receipt_attach_count + expense_confirm_count),
        "reinforcement_pending_count": int(reinforcement_pending_count),
        "package_status": str(preflight.get("status") or "warn"),
        "package_ready": str(preflight.get("status") or "warn") == "pass",
        "can_download_package": bool(can_download_package(int(user_pk))),
        "package_top_issue_count": int(len(preflight.get("top_issues") or [])),
        "receipt_reflected_expense_krw": int(getattr(tax_est, "receipt_reflected_expense_krw", 0) or 0),
        "receipt_pending_expense_krw": int(getattr(tax_est, "receipt_pending_expense_krw", 0) or 0),
        "buffer_shortage_krw": int(getattr(tax_est, "buffer_shortage_krw", 0) or 0),
    }


def build_seasonal_cards(season_focus: str, facts: dict[str, Any], urls: dict[str, str] | None = None) -> list[dict[str, Any]]:
    urls = dict(urls or {})
    review_url = str(urls.get("review") or "#")
    tax_url = str(urls.get("tax_buffer") or "#")
    package_url = str(urls.get("package") or "#")
    profile_url = str(urls.get("profile") or tax_url or "#")

    has_transactions = bool(facts.get("has_transactions"))
    accuracy_gap = bool(facts.get("tax_accuracy_gap"))
    profile_completion_percent = int(facts.get("profile_completion_percent") or 0)
    receipt_pending_count = int(facts.get("receipt_pending_count") or 0)
    reinforcement_pending_count = int(facts.get("reinforcement_pending_count") or 0)
    package_ready = bool(facts.get("package_ready"))
    package_status = str(facts.get("package_status") or "warn")
    can_download_package_flag = bool(facts.get("can_download_package"))
    buffer_shortage_krw = int(facts.get("buffer_shortage_krw") or 0)
    receipt_pending_expense_krw = int(facts.get("receipt_pending_expense_krw") or 0)

    cards: list[dict[str, Any]] = []

    if season_focus == MAY_FILING_FOCUS:
        cards.extend(
            [
                {
                    "card_type": "may_accuracy",
                    "title": "작년 수입과 비용 정리 전에 숫자부터 맞춰요",
                    "summary": (
                        "이미 빠진 세금과 기본 정보가 비어 있어 신고 직전 숫자가 흔들릴 수 있어요."
                        if accuracy_gap
                        else "기본 정보가 들어 있어 신고 전에 숫자를 다시 보기 좋은 상태예요."
                    ),
                    "cta_label": "기본 정보 보완하기" if accuracy_gap else "세금 보관함 보기",
                    "cta_url": profile_url if accuracy_gap else tax_url,
                    "cta_target": _cta_target_for_card(profile_target=accuracy_gap, default_target="tax_buffer"),
                    "priority": 1,
                    "completion_action": (seasonal_card_completion_action("may_accuracy") if accuracy_gap else ""),
                    "completion_state": _completion_state(
                        done=not accuracy_gap,
                        partial=accuracy_gap and profile_completion_percent > 0,
                    ),
                },
                {
                    "card_type": "may_receipt_cleanup",
                    "title": "반영 대기 영수증을 신고 전에 줄여요",
                    "summary": (
                        f"반영 대기 {receipt_pending_count}건, 보강 대기 {reinforcement_pending_count}건이 있어요."
                        if (receipt_pending_count or reinforcement_pending_count)
                        else "이번 달 기준으로 반영 대기 영수증은 크지 않아요."
                    ),
                    "cta_label": "정리하기",
                    "cta_url": review_url,
                    "cta_target": "review",
                    "priority": 2,
                    "completion_action": seasonal_card_completion_action("may_receipt_cleanup"),
                    "completion_state": _completion_state(
                        done=(receipt_pending_count == 0 and reinforcement_pending_count == 0),
                        partial=(receipt_pending_count > 0 or reinforcement_pending_count > 0),
                    ),
                },
                {
                    "card_type": "may_package_ready",
                    "title": "세무사에게 넘기기 전 전달 자료를 점검해요",
                    "summary": (
                        "전달 전 점검이 통과됐어요. 지금 내려받아 전달 준비를 마무리할 수 있어요."
                        if package_ready
                        else "누락과 메모를 조금만 더 정리하면 전달 자료가 더 분명해져요."
                    ),
                    "cta_label": "패키지 보기" if can_download_package_flag else "전달 자료 상태 보기",
                    "cta_url": package_url,
                    "cta_target": "package",
                    "priority": 3,
                    "completion_action": seasonal_card_completion_action("may_package_ready"),
                    "completion_state": _completion_state(
                        done=package_ready,
                        partial=has_transactions and package_status in {"warn", "fail"},
                    ),
                },
            ]
        )
    elif season_focus == NOVEMBER_PREPAYMENT_FOCUS:
        cards.extend(
            [
                {
                    "card_type": "november_halfyear_check",
                    "title": "상반기 기준으로 숫자가 얼마나 흔들릴지 먼저 봐요",
                    "summary": (
                        "돈 받을 때 미리 빠진 세금과 기본 정보가 비어 있으면 11월 점검 숫자가 흐려져요."
                        if accuracy_gap
                        else "기본 정보가 있어 상반기 기준 점검을 이어가기 좋은 상태예요."
                    ),
                    "cta_label": "세금 보관함 보기" if not accuracy_gap else "기본 정보 보완하기",
                    "cta_url": tax_url if not accuracy_gap else profile_url,
                    "cta_target": _cta_target_for_card(profile_target=accuracy_gap, default_target="tax_buffer"),
                    "priority": 1,
                    "completion_action": (seasonal_card_completion_action("november_halfyear_check") if accuracy_gap else ""),
                    "completion_state": _completion_state(
                        done=not accuracy_gap,
                        partial=accuracy_gap and profile_completion_percent > 0,
                    ),
                },
                {
                    "card_type": "november_receipt_reinforce",
                    "title": "상반기 기준에 들어갈 비용부터 먼저 보강해요",
                    "summary": (
                        f"반영 대기 {receipt_pending_count}건, 비용 반영 보류 {receipt_pending_expense_krw:,}원이 남아 있어요."
                        if (receipt_pending_count or receipt_pending_expense_krw or reinforcement_pending_count)
                        else "지금은 비용 보강이 급한 항목이 많지 않아요."
                    ),
                    "cta_label": "반영 대기 정리하기",
                    "cta_url": review_url,
                    "cta_target": "review",
                    "priority": 2,
                    "completion_action": seasonal_card_completion_action("november_receipt_reinforce"),
                    "completion_state": _completion_state(
                        done=(receipt_pending_count == 0 and reinforcement_pending_count == 0),
                        partial=(receipt_pending_count > 0 or reinforcement_pending_count > 0),
                    ),
                },
                {
                    "card_type": "november_buffer_check",
                    "title": "지금 기준으로 부족한 보관액이 있는지 확인해요",
                    "summary": (
                        f"현재 기준으로 세금 보관이 {buffer_shortage_krw:,}원 부족해요."
                        if buffer_shortage_krw > 0
                        else "지금 기준으로는 큰 부족 없이 보고 있는 상태예요."
                    ),
                    "cta_label": "부족분 확인하기",
                    "cta_url": tax_url,
                    "cta_target": "tax_buffer",
                    "priority": 3,
                    "completion_action": seasonal_card_completion_action("november_buffer_check"),
                    "completion_state": _completion_state(
                        done=bool(has_transactions and buffer_shortage_krw <= 0 and not accuracy_gap),
                        partial=has_transactions,
                    ),
                },
            ]
        )
    else:
        cards.extend(
            [
                {
                    "card_type": "offseason_monthly_review",
                    "title": "이번 달 정리부터 끝내 두면 다음 시즌이 편해져요",
                    "summary": (
                        f"지금 반영 대기 {receipt_pending_count + reinforcement_pending_count}건부터 정리하면 다음 시즌 전에 미뤄질 일이 줄어요."
                        if (receipt_pending_count or reinforcement_pending_count)
                        else "이번 달 기준으로 급한 정리 항목은 많이 남아 있지 않아요."
                    ),
                    "cta_label": "정리하기",
                    "cta_url": review_url,
                    "cta_target": "review",
                    "priority": 0,
                    "completion_action": seasonal_card_completion_action("offseason_monthly_review"),
                    "completion_state": _completion_state(
                        done=(receipt_pending_count == 0 and reinforcement_pending_count == 0 and has_transactions),
                        partial=(receipt_pending_count > 0 or reinforcement_pending_count > 0),
                    ),
                },
                {
                    "card_type": "offseason_accuracy",
                    "title": "기본 정보만 맞춰 두면 다음 시즌 숫자가 덜 흔들려요",
                    "summary": (
                        "3.3%와 이미 빠진 세금만 확인해 두면 다음 시즌 숫자가 덜 흔들려요."
                        if accuracy_gap
                        else "이미 빠진 세금까지 들어 있어요. 지금 보이는 숫자만 확인하면 돼요."
                    ),
                    "cta_label": "3.3%·빠진 세금 확인하기" if accuracy_gap else "세금 보관함 보기",
                    "cta_url": profile_url if accuracy_gap else tax_url,
                    "cta_target": _cta_target_for_card(profile_target=accuracy_gap, default_target="tax_buffer"),
                    "priority": 2,
                    "completion_action": (seasonal_card_completion_action("offseason_accuracy") if accuracy_gap else ""),
                    "completion_state": _completion_state(
                        done=not accuracy_gap,
                        partial=accuracy_gap and profile_completion_percent > 0,
                    ),
                },
                {
                    "card_type": "offseason_package_ready",
                    "title": "전달 자료를 미리 정리해 두면 급해지지 않아요",
                    "summary": (
                        "전달 준비가 거의 끝나 있어요."
                        if package_ready
                        else "세무사 보내기 전 점검표만 먼저 보면 나중에 다시 설명할 일이 줄어요."
                    ),
                    "cta_label": "패키지 보기",
                    "cta_url": package_url,
                    "cta_target": "package",
                    "priority": 3,
                    "completion_action": seasonal_card_completion_action("offseason_package_ready"),
                    "completion_state": _completion_state(
                        done=package_ready,
                        partial=has_transactions and package_status in {"warn", "fail"},
                    ),
                },
            ]
        )

    adjusted_cards = _apply_seasonal_priority_inference(cards, facts)
    return sorted(adjusted_cards, key=_priority_rank)[:3]


def build_seasonal_experience(
    *,
    user_pk: int,
    month_key: str | None,
    urls: dict[str, str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    ref = today or date.today()
    season_focus = determine_season_focus(ref)
    season_label = str(SEASONAL_WINDOWS.get(season_focus, {}).get("label") or "비시즌")
    facts = collect_seasonal_user_state(user_pk=int(user_pk), month_key=month_key, today=ref)
    cards = build_seasonal_cards(season_focus, facts, urls)

    if season_focus == MAY_FILING_FOCUS:
        title = "이번 시즌은 작년 수입과 비용을 정리하는 달이에요"
        summary = "지금 3가지만 보면 신고 전에 숫자, 영수증, 전달 자료를 한 번에 정리할 수 있어요."
        strength = "strong"
    elif season_focus == NOVEMBER_PREPAYMENT_FOCUS:
        title = "이번 시즌은 상반기 기준으로 미리 점검하는 시기예요"
        summary = "지금 부족한 것부터 정리하면 11월에 숫자가 갑자기 흔들리는 일을 줄일 수 있어요."
        strength = "strong"
    else:
        title = "이번 달 리듬에 맞춰 필요한 것만 정리해요"
        summary = "다음 시즌에 급해지지 않도록 지금 보이는 결과에서 필요한 것만 이어서 하면 돼요."
        strength = "soft"

    return {
        "season_focus": season_focus,
        "season_label": season_label,
        "title": title,
        "summary": summary,
        "strength": strength,
        "cards": cards,
        "facts": facts,
    }


def build_seasonal_screen_context(seasonal_experience: dict[str, Any] | None, screen: str) -> dict[str, Any] | None:
    if not isinstance(seasonal_experience, dict):
        return None
    season_focus = str(seasonal_experience.get("season_focus") or OFF_SEASON)
    cards = seasonal_experience.get("cards") or []
    if not isinstance(cards, list) or not cards:
        return None

    allowed = SEASONAL_SCREEN_CARD_TYPES.get(screen, {}).get(season_focus) or ()
    selected = next((card for card in cards if str(card.get("card_type") or "") in allowed), cards[0] if cards else None)
    if not isinstance(selected, dict):
        return None

    completion_state = str(selected.get("completion_state") or "todo")
    if completion_state == "done":
        completion_label = "완료"
    elif completion_state == "in_progress":
        completion_label = "진행 중"
    else:
        completion_label = "지금 확인"

    cta_url = str(selected.get("cta_url") or "#")
    cta_label = str(selected.get("cta_label") or "확인하기")
    cta_target = str(selected.get("cta_target") or "unknown")
    facts = dict(seasonal_experience.get("facts") or {})
    summary = str(selected.get("summary") or "")
    if screen == "review" and cta_target == "review":
        cta_url = _append_query_params(cta_url, {})
        if "#" not in cta_url:
            cta_url = f"{cta_url}#review-worklist"
        cta_label = "반영 대기 항목부터 정리하기"
        pending_count = int(facts.get("receipt_pending_count") or 0) + int(facts.get("reinforcement_pending_count") or 0)
        if pending_count > 0:
            summary = f"지금은 반영 대기 {pending_count}건부터 열어 보고, follow-up이나 보강이 필요한 항목부터 처리하면 돼요."
    elif screen == "tax_buffer" and cta_target == "tax_buffer":
        if "#" not in cta_url:
            cta_url = f"{cta_url}#tax-buffer-kpis"
        cta_label = "예상세금·보관액 바로 보기"
        shortage = int(facts.get("buffer_shortage_krw") or 0)
        if shortage > 0:
            summary = f"지금 보이는 예상세금과 부족 보관액 {shortage:,}원부터 확인하면 다음 시즌 전에 덜 급해져요."
        else:
            summary = "지금 계산된 예상세금과 보관액부터 한 번만 다시 보면 다음 시즌 준비가 가벼워져요."
    elif screen == "package" and cta_target == "package":
        if "#" not in cta_url:
            cta_url = f"{cta_url}#package-readiness"
        cta_label = "세무사 보내기 전 마지막 점검 보기"
        summary = "지금은 전달 준비 상태와 위에서부터 점검할 항목만 먼저 보면 돼요."

    return {
        "season_label": str(seasonal_experience.get("season_label") or ""),
        "season_focus": season_focus,
        "source_screen": str(screen or ""),
        "card_type": str(selected.get("card_type") or ""),
        "title": str(selected.get("title") or ""),
        "summary": summary,
        "cta_label": cta_label,
        "cta_url": cta_url,
        "cta_target": str(selected.get("cta_target") or "unknown"),
        "priority": int(selected.get("priority") or 0),
        "priority_base": int(selected.get("priority_base") or selected.get("priority") or 0),
        "priority_effective": int(selected.get("priority_effective") or selected.get("priority") or 0),
        "priority_adjustment_score": int(selected.get("priority_adjustment_score") or 0),
        "priority_adjustment_reason": str(selected.get("priority_adjustment_reason") or ""),
        "completion_action": str(selected.get("completion_action") or seasonal_card_completion_action(str(selected.get("card_type") or ""))),
        "completion_state": completion_state,
        "completion_label": completion_label,
        "strength": str(seasonal_experience.get("strength") or "soft"),
    }
