from __future__ import annotations

from typing import Any

from core.extensions import db
from domain.models import ActionLog


INPUT_FUNNEL_EVENT_NAMES = {
    "tax_recovery_cta_shown",
    "tax_recovery_cta_clicked",
    "tax_inline_income_classification_shown",
    "tax_inline_income_classification_saved",
    "tax_basic_next_step_viewed",
    "tax_basic_next_step_saved",
    "tax_basic_step_viewed",
    "tax_basic_step_saved",
    "tax_advanced_step_viewed",
    "tax_advanced_step_saved",
    "tax_recovery_completed",
    "nhis_recovery_cta_shown",
    "nhis_recovery_cta_clicked",
    "nhis_inline_membership_type_shown",
    "nhis_inline_membership_type_saved",
    "nhis_detail_next_step_viewed",
    "nhis_detail_next_step_saved",
    "nhis_membership_step_viewed",
    "nhis_membership_step_saved",
    "nhis_detail_step_viewed",
    "nhis_detail_step_saved",
    "nhis_recovery_completed",
}

SEASONAL_CARD_EVENT_NAMES = {
    "seasonal_card_shown",
    "seasonal_card_clicked",
    "seasonal_card_landed",
    "seasonal_card_completed",
}


def _norm_level(raw: Any) -> str:
    level = str(raw or "").strip().lower()
    if level in {"exact_ready", "high_confidence", "limited", "blocked"}:
        return level
    return "limited"


def _norm_reason(raw: Any) -> str:
    reason = str(raw or "").strip().lower()
    return reason or "unknown"


def _norm_completion_state(raw: Any) -> str:
    state = str(raw or "").strip().lower()
    if state in {"todo", "in_progress", "done"}:
        return state
    return "todo"


def _norm_month_key(raw: Any) -> str:
    month_key = str(raw or "").strip()
    if len(month_key) == 7 and month_key[4] == "-":
        return month_key
    return ""


def record_input_funnel_event(
    *,
    user_pk: int,
    event: str,
    route: str = "",
    screen: str = "",
    accuracy_level_before: str | None = None,
    accuracy_level_after: str | None = None,
    reason_code: str | None = None,
    reason_code_before: str | None = None,
    reason_code_after: str | None = None,
    target_ids: list[int] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    event_name = str(event or "").strip().lower()
    if event_name not in INPUT_FUNNEL_EVENT_NAMES:
        return

    safe_extra = dict(extra or {})
    before_reason = _norm_reason(reason_code_before if reason_code_before is not None else reason_code)
    after_reason = _norm_reason(reason_code_after if reason_code_after is not None else reason_code)
    payload = {
        "metric_type": "input_funnel",
        "metric_event": event_name,
        "route": str(route or "").strip(),
        "screen": str(screen or "").strip(),
        "accuracy_level_before": _norm_level(accuracy_level_before),
        "accuracy_level_after": _norm_level(accuracy_level_after),
        "reason_code": after_reason,
        "reason_code_before": before_reason,
        "reason_code_after": after_reason,
        "extra": safe_extra,
    }
    row = ActionLog(
        user_pk=int(user_pk),
        action_type="label_update",
        target_ids=[int(v) for v in (target_ids or []) if int(v) > 0],
        before_state=payload,
        after_state={},
        is_reverted=False,
    )
    try:
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()


def record_seasonal_card_event(
    *,
    user_pk: int,
    event: str,
    season_focus: str,
    card_type: str,
    cta_target: str,
    source_screen: str,
    priority: int | None = None,
    completion_state_before: str | None = None,
    completion_state_after: str | None = None,
    month_key: str | None = None,
    route: str = "",
    target_ids: list[int] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    event_name = str(event or "").strip().lower()
    if event_name not in SEASONAL_CARD_EVENT_NAMES:
        return

    before_state = _norm_completion_state(completion_state_before)
    after_state = _norm_completion_state(completion_state_after if completion_state_after is not None else completion_state_before)
    safe_extra = dict(extra or {})
    payload = {
        "metric_type": "seasonal_ux",
        "metric_event": event_name,
        "route": str(route or "").strip(),
        "season_focus": str(season_focus or "").strip().lower() or "off_season",
        "card_type": str(card_type or "").strip().lower() or "unknown",
        "cta_target": str(cta_target or "").strip().lower() or "unknown",
        "source_screen": str(source_screen or "").strip().lower() or "unknown",
        "priority": int(priority or 0),
        "completion_state_before": before_state,
        "completion_state_after": after_state,
        "month_key": _norm_month_key(month_key),
        "extra": safe_extra,
    }
    row = ActionLog(
        user_pk=int(user_pk),
        action_type="label_update",
        target_ids=[int(v) for v in (target_ids or []) if int(v) > 0],
        before_state=payload,
        after_state={},
        is_reverted=False,
    )
    try:
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
