from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FUNNEL_ORDER = [
    "seasonal_card_shown",
    "seasonal_card_clicked",
    "seasonal_card_landed",
    "seasonal_card_completed",
]

INTERPRETATION_THRESHOLDS = {
    "min_shown_for_directional_read": 30,
    "min_clicked_for_directional_read": 10,
    "min_completed_for_completion_read": 5,
    "min_shown_for_provisional_copy_review": 5,
    "min_clicked_for_provisional_friction_review": 1,
}


def _pct(numerator: int, denominator: int) -> float:
    if int(denominator) <= 0:
        return 0.0
    return round((int(numerator) / int(denominator)) * 100.0, 2)


def _empty_counts() -> dict[str, int]:
    return {event: 0 for event in FUNNEL_ORDER}


def _summary_row(key: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    shown = int(counts.get("seasonal_card_shown", 0))
    clicked = int(counts.get("seasonal_card_clicked", 0))
    landed = int(counts.get("seasonal_card_landed", 0))
    completed = int(counts.get("seasonal_card_completed", 0))
    return {
        **key,
        "shown_count": shown,
        "clicked_count": clicked,
        "landed_count": landed,
        "completed_count": completed,
        "ctr": _pct(clicked, shown),
        "landed_rate": _pct(landed, clicked),
        "completion_rate_from_click": _pct(completed, clicked),
        "completion_rate_from_show": _pct(completed, shown),
    }


def build_interpretation(
    audit_payload: dict[str, Any],
    *,
    source_report: str = "",
    generated_at: str = "",
) -> dict[str, Any]:
    thresholds = dict(INTERPRETATION_THRESHOLDS)
    by_card = list(audit_payload.get("by_card") or [])
    overall = dict(audit_payload.get("overall") or {})
    seasonal_rows = int(audit_payload.get("seasonal_ux_rows") or 0)

    def enough_directional(row: dict[str, Any]) -> bool:
        return (
            int(row.get("shown_count") or 0) >= thresholds["min_shown_for_directional_read"]
            and int(row.get("clicked_count") or 0) >= thresholds["min_clicked_for_directional_read"]
            and int(row.get("completed_count") or 0) >= thresholds["min_completed_for_completion_read"]
        )

    def low_ctr_signal(row: dict[str, Any]) -> bool:
        return (
            int(row.get("shown_count") or 0) >= thresholds["min_shown_for_provisional_copy_review"]
            and int(row.get("clicked_count") or 0) == 0
        )

    def completion_friction_signal(row: dict[str, Any]) -> bool:
        return (
            int(row.get("clicked_count") or 0) >= thresholds["min_clicked_for_provisional_friction_review"]
            and int(row.get("landed_count") or 0) >= 1
            and int(row.get("completed_count") or 0) == 0
        )

    has_enough_data = any(enough_directional(row) for row in by_card)
    insufficient_reason = ""
    if not has_enough_data:
        insufficient_reason = (
            "No card reached the minimum shown/clicked/completed thresholds for directional priority changes."
        )

    card_rows: list[dict[str, Any]] = []
    copy_review_cards: list[dict[str, Any]] = []
    completion_review_cards: list[dict[str, Any]] = []
    raise_candidates: list[dict[str, Any]] = []
    lower_candidates: list[dict[str, Any]] = []

    for row in by_card:
        interpreted = "insufficient_data"
        recommendation = "hold"
        if enough_directional(row):
            ctr = float(row.get("ctr") or 0.0)
            completion_from_click = float(row.get("completion_rate_from_click") or 0.0)
            if ctr >= 20.0 and completion_from_click >= 30.0:
                interpreted = "healthy_directional_signal"
                recommendation = "consider_raise"
                raise_candidates.append(
                    {
                        "season_focus": row.get("season_focus"),
                        "card_type": row.get("card_type"),
                        "source_screen": row.get("source_screen"),
                        "cta_target": row.get("cta_target"),
                        "reason": "high ctr and healthy completion rate",
                    }
                )
            elif ctr < 20.0:
                interpreted = "low_ctr_directional_signal"
                recommendation = "consider_lower_or_rewrite"
                lower_candidates.append(
                    {
                        "season_focus": row.get("season_focus"),
                        "card_type": row.get("card_type"),
                        "source_screen": row.get("source_screen"),
                        "cta_target": row.get("cta_target"),
                        "reason": "enough data with low ctr",
                    }
                )
            elif completion_from_click < 30.0:
                interpreted = "completion_friction_directional_signal"
                recommendation = "completion_friction_review"
            else:
                interpreted = "directional_signal_hold"
        elif low_ctr_signal(row):
            interpreted = "low_ctr_signal_but_insufficient_data"
            recommendation = "copy_review"
            copy_review_cards.append(
                {
                    "season_focus": row.get("season_focus"),
                    "card_type": row.get("card_type"),
                    "source_screen": row.get("source_screen"),
                    "cta_target": row.get("cta_target"),
                    "reason": "shown exists but clicked is zero",
                }
            )
        elif completion_friction_signal(row):
            interpreted = "completion_friction_signal_but_insufficient_data"
            recommendation = "completion_friction_review"
            completion_review_cards.append(
                {
                    "season_focus": row.get("season_focus"),
                    "card_type": row.get("card_type"),
                    "source_screen": row.get("source_screen"),
                    "cta_target": row.get("cta_target"),
                    "reason": "clicked and landed happened but completed is zero",
                }
            )
        card_rows.append(
            {
                "season_focus": row.get("season_focus"),
                "card_type": row.get("card_type"),
                "source_screen": row.get("source_screen"),
                "cta_target": row.get("cta_target"),
                "shown_count": int(row.get("shown_count") or 0),
                "clicked_count": int(row.get("clicked_count") or 0),
                "completed_count": int(row.get("completed_count") or 0),
                "ctr": float(row.get("ctr") or 0.0),
                "completion_rate_from_click": float(row.get("completion_rate_from_click") or 0.0),
                "enough_for_directional_read": enough_directional(row),
                "interpretation": interpreted,
                "recommendation": recommendation,
            }
        )

    screen_counts: dict[str, dict[str, int]] = defaultdict(_empty_counts)
    for row in by_card:
        screen_key = str(row.get("source_screen") or "unknown")
        screen_counts[screen_key]["seasonal_card_shown"] += int(row.get("shown_count") or 0)
        screen_counts[screen_key]["seasonal_card_clicked"] += int(row.get("clicked_count") or 0)
        screen_counts[screen_key]["seasonal_card_landed"] += int(row.get("landed_count") or 0)
        screen_counts[screen_key]["seasonal_card_completed"] += int(row.get("completed_count") or 0)

    screen_rows: list[dict[str, Any]] = []
    for screen_key, counts in sorted(screen_counts.items(), key=lambda item: item[0]):
        row = _summary_row({"source_screen": screen_key}, counts)
        interpreted = "insufficient_data"
        if int(row.get("shown_count") or 0) >= thresholds["min_shown_for_directional_read"]:
            interpreted = "directional_signal"
        screen_rows.append(
            {
                "source_screen": screen_key,
                "shown_count": int(row.get("shown_count") or 0),
                "clicked_count": int(row.get("clicked_count") or 0),
                "landed_count": int(row.get("landed_count") or 0),
                "completed_count": int(row.get("completed_count") or 0),
                "ctr": float(row.get("ctr") or 0.0),
                "completion_rate_from_click": float(row.get("completion_rate_from_click") or 0.0),
                "interpretation": interpreted,
            }
        )

    provisional_recommendations: list[str] = []
    if copy_review_cards:
        provisional_recommendations.append(
            "Off-season accuracy cards have repeated shown events but zero clicks, so CTA copy should become more concrete before changing priority."
        )
    if completion_review_cards:
        provisional_recommendations.append(
            "The review seasonal context card is the only card with click+landed activity, so keep its priority but reduce same-screen CTA friction."
        )
    if not has_enough_data:
        provisional_recommendations.append(
            "Do not reorder may/november cards yet because current data is below directional thresholds."
        )

    return {
        "generated_at": generated_at,
        "source_report": source_report,
        "thresholds": thresholds,
        "has_enough_data": has_enough_data,
        "insufficient_data_reason": insufficient_reason,
        "overall_summary": {
            "seasonal_ux_rows": seasonal_rows,
            "shown_count": int(overall.get("shown_count") or 0),
            "clicked_count": int(overall.get("clicked_count") or 0),
            "landed_count": int(overall.get("landed_count") or 0),
            "completed_count": int(overall.get("completed_count") or 0),
            "observed_season_focuses": [row.get("season_focus") for row in audit_payload.get("by_season") or []],
        },
        "by_card_interpretation": card_rows,
        "by_screen_interpretation": screen_rows,
        "candidates_for_priority_raise": raise_candidates,
        "candidates_for_priority_lower": lower_candidates,
        "cards_needing_copy_review": copy_review_cards,
        "cards_needing_completion_friction_review": completion_review_cards,
        "provisional_recommendations": provisional_recommendations,
    }


def run_audit(*, days: int, output: str = "", user_pk: int | None = None, limit: int = 5000) -> dict[str, Any]:
    from app import create_app
    from core.time import utcnow
    from domain.models import ActionLog

    app = create_app()
    with app.app_context():
        since = utcnow() - timedelta(days=max(1, int(days)))
        query = (
            ActionLog.query.filter(ActionLog.action_type == "label_update")
            .filter(ActionLog.created_at >= since)
            .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
        )
        if user_pk is not None:
            query = query.filter(ActionLog.user_pk == int(user_pk))
        if int(limit) > 0:
            query = query.limit(int(limit))
        rows = query.all()

        overall_counts = Counter()
        by_season_counts: dict[str, Counter[str]] = defaultdict(Counter)
        by_card_counts: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)

        total_rows = 0
        for row in rows:
            payload = row.before_state if isinstance(row.before_state, dict) else {}
            if str(payload.get("metric_type") or "").strip().lower() != "seasonal_ux":
                continue
            event = str(payload.get("metric_event") or "").strip().lower()
            if event not in FUNNEL_ORDER:
                continue
            season_focus = str(payload.get("season_focus") or "off_season").strip().lower() or "off_season"
            card_type = str(payload.get("card_type") or "unknown").strip().lower() or "unknown"
            source_screen = str(payload.get("source_screen") or "unknown").strip().lower() or "unknown"
            cta_target = str(payload.get("cta_target") or "unknown").strip().lower() or "unknown"

            total_rows += 1
            overall_counts[event] += 1
            by_season_counts[season_focus][event] += 1
            by_card_counts[(season_focus, card_type, source_screen, cta_target)][event] += 1

        overall = _summary_row({"scope": "overall"}, dict(_empty_counts() | overall_counts))
        by_season = [
            _summary_row({"season_focus": season_focus}, dict(_empty_counts() | counts))
            for season_focus, counts in sorted(by_season_counts.items(), key=lambda item: item[0])
        ]
        by_card = [
            _summary_row(
                {
                    "season_focus": season_focus,
                    "card_type": card_type,
                    "source_screen": source_screen,
                    "cta_target": cta_target,
                },
                dict(_empty_counts() | counts),
            )
            for (season_focus, card_type, source_screen, cta_target), counts in sorted(
                by_card_counts.items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3]),
            )
        ]

        low_ctr_cards = [
            row
            for row in by_card
            if int(row.get("shown_count") or 0) > 0 and float(row.get("ctr") or 0.0) < 20.0
        ]
        low_completion_cards = [
            row
            for row in by_card
            if int(row.get("clicked_count") or 0) > 0 and float(row.get("completion_rate_from_click") or 0.0) < 30.0
        ]

        payload = {
            "window_days": int(days),
            "query_limit": int(limit),
            "user_pk": int(user_pk) if user_pk is not None else None,
            "seasonal_ux_rows": int(total_rows),
            "overall": overall,
            "by_season": by_season,
            "by_card": by_card,
            "low_ctr_cards": low_ctr_cards,
            "low_completion_cards": low_completion_cards,
        }

        if output:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit seasonal UX shown/clicked/landed/completed funnels.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days.")
    parser.add_argument("--limit", type=int, default=5000, help="Max ActionLog rows to scan.")
    parser.add_argument("--user-pk", type=int, default=None, help="Optional single user filter.")
    parser.add_argument("--output", type=str, default="", help="Optional output JSON path.")
    parser.add_argument(
        "--interpretation-output",
        type=str,
        default="",
        help="Optional interpretation JSON path based on the audit result.",
    )
    args = parser.parse_args()

    payload = run_audit(
        days=int(args.days),
        output=str(args.output or "").strip(),
        user_pk=(int(args.user_pk) if args.user_pk is not None else None),
        limit=int(args.limit),
    )
    interpretation_output = str(args.interpretation_output or "").strip()
    if interpretation_output:
        from core.time import utcnow

        interpretation = build_interpretation(
            payload,
            source_report=str(args.output or "").strip(),
            generated_at=utcnow().replace(microsecond=0).isoformat(),
        )
        out_path = Path(interpretation_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(interpretation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
