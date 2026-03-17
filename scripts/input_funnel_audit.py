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


TAX_FUNNEL_ORDER = [
    "tax_inline_income_classification_shown",
    "tax_inline_income_classification_saved",
    "tax_basic_next_step_viewed",
    "tax_basic_next_step_saved",
    "tax_recovery_completed",
]

TAX_CTA_FUNNEL_ORDER = [
    "tax_recovery_cta_shown",
    "tax_recovery_cta_clicked",
]

NHIS_FUNNEL_ORDER = [
    "nhis_inline_membership_type_shown",
    "nhis_inline_membership_type_saved",
    "nhis_detail_next_step_viewed",
    "nhis_detail_next_step_saved",
    "nhis_recovery_completed",
]

NHIS_CTA_FUNNEL_ORDER = [
    "nhis_recovery_cta_shown",
    "nhis_recovery_cta_clicked",
]


def _pct(numerator: int, denominator: int) -> float:
    if int(denominator) <= 0:
        return 0.0
    return round((int(numerator) / int(denominator)) * 100.0, 2)


def _funnel_rows(counter: Counter[str], order: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "event": event,
            "count": int(counter.get(event, 0)),
        }
        for event in order
    ]


def _conversion_rows(counter: Counter[str], order: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prev_count = int(counter.get(order[0], 0)) if order else 0
    for event in order:
        count = int(counter.get(event, 0))
        rows.append(
            {
                "event": event,
                "count": count,
                "from_previous_percent": _pct(count, prev_count) if prev_count > 0 else 0.0,
            }
        )
        prev_count = count
    return rows


def run_audit(*, days: int, limit: int, user_pk: int | None) -> dict[str, Any]:
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

        event_counter: Counter[str] = Counter()
        reason_counter: Counter[str] = Counter()
        before_level_counter: Counter[str] = Counter()
        after_level_counter: Counter[str] = Counter()
        screen_counter: Counter[str] = Counter()
        route_counter: Counter[str] = Counter()
        user_event_counter: dict[str, set[int]] = defaultdict(set)

        total_rows = 0
        for row in rows:
            payload = row.before_state if isinstance(row.before_state, dict) else {}
            if str(payload.get("metric_type") or "").strip().lower() != "input_funnel":
                continue
            event = str(payload.get("metric_event") or "").strip().lower()
            if not event:
                continue
            total_rows += 1
            event_counter[event] += 1
            reason_counter[str(payload.get("reason_code") or "unknown").strip().lower() or "unknown"] += 1
            before_level_counter[str(payload.get("accuracy_level_before") or "unknown").strip().lower() or "unknown"] += 1
            after_level_counter[str(payload.get("accuracy_level_after") or "unknown").strip().lower() or "unknown"] += 1
            screen_counter[str(payload.get("screen") or "unknown").strip().lower() or "unknown"] += 1
            route_counter[str(payload.get("route") or "unknown").strip().lower() or "unknown"] += 1
            user_event_counter[event].add(int(row.user_pk))

        tax_events = Counter({k: v for k, v in event_counter.items() if k.startswith("tax_")})
        nhis_events = Counter({k: v for k, v in event_counter.items() if k.startswith("nhis_")})

        return {
            "window_days": int(days),
            "query_limit": int(limit),
            "user_pk": int(user_pk) if user_pk is not None else None,
            "input_funnel_rows": int(total_rows),
            "tax_funnel": {
                "events": _funnel_rows(tax_events, TAX_FUNNEL_ORDER),
                "conversions": _conversion_rows(tax_events, TAX_FUNNEL_ORDER),
            },
            "tax_cta_funnel": {
                "events": _funnel_rows(tax_events, TAX_CTA_FUNNEL_ORDER),
                "conversions": _conversion_rows(tax_events, TAX_CTA_FUNNEL_ORDER),
            },
            "nhis_funnel": {
                "events": _funnel_rows(nhis_events, NHIS_FUNNEL_ORDER),
                "conversions": _conversion_rows(nhis_events, NHIS_FUNNEL_ORDER),
            },
            "nhis_cta_funnel": {
                "events": _funnel_rows(nhis_events, NHIS_CTA_FUNNEL_ORDER),
                "conversions": _conversion_rows(nhis_events, NHIS_CTA_FUNNEL_ORDER),
            },
            "summary": {
                "primary_metric": "inline_save_funnel",
                "tax_inline_shown": int(tax_events.get("tax_inline_income_classification_shown", 0)),
                "tax_inline_saved": int(tax_events.get("tax_inline_income_classification_saved", 0)),
                "tax_recovery_completed": int(tax_events.get("tax_recovery_completed", 0)),
                "nhis_inline_shown": int(nhis_events.get("nhis_inline_membership_type_shown", 0)),
                "nhis_inline_saved": int(nhis_events.get("nhis_inline_membership_type_saved", 0)),
                "nhis_recovery_completed": int(nhis_events.get("nhis_recovery_completed", 0)),
                "tax_inline_save_rate_from_shown_percent": _pct(
                    int(tax_events.get("tax_inline_income_classification_saved", 0)),
                    int(tax_events.get("tax_inline_income_classification_shown", 0)),
                ),
                "nhis_inline_save_rate_from_shown_percent": _pct(
                    int(nhis_events.get("nhis_inline_membership_type_saved", 0)),
                    int(nhis_events.get("nhis_inline_membership_type_shown", 0)),
                ),
            },
            "unique_users_by_event": {
                k: int(len(v)) for k, v in sorted(user_event_counter.items(), key=lambda x: x[0])
            },
            "reason_distribution": [
                {"reason_code": key, "count": int(count)}
                for key, count in sorted(reason_counter.items(), key=lambda x: (-int(x[1]), str(x[0])))
            ],
            "accuracy_level_before_distribution": [
                {"accuracy_level_before": key, "count": int(count)}
                for key, count in sorted(before_level_counter.items(), key=lambda x: (-int(x[1]), str(x[0])))
            ],
            "accuracy_level_after_distribution": [
                {"accuracy_level_after": key, "count": int(count)}
                for key, count in sorted(after_level_counter.items(), key=lambda x: (-int(x[1]), str(x[0])))
            ],
            "screen_distribution": [
                {"screen": key, "count": int(count)}
                for key, count in sorted(screen_counter.items(), key=lambda x: (-int(x[1]), str(x[0])))
            ],
            "route_distribution": [
                {"route": key, "count": int(count)}
                for key, count in sorted(route_counter.items(), key=lambda x: (-int(x[1]), str(x[0])))
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Input funnel audit for tax/nhis recovery flows.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30).")
    parser.add_argument("--limit", type=int, default=5000, help="Max ActionLog rows to scan (default: 5000).")
    parser.add_argument("--user-pk", type=int, default=None, help="Optional single user filter.")
    parser.add_argument("--output", type=str, default="", help="Optional output JSON path.")
    args = parser.parse_args()

    payload = run_audit(
        days=int(args.days),
        limit=int(args.limit),
        user_pk=(int(args.user_pk) if args.user_pk is not None else None),
    )

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    output_path = str(args.output or "").strip()
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
