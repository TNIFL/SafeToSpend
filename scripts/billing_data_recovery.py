from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from app import create_app
from core.extensions import db
from domain.models import BillingMethod, BillingMethodRegistrationAttempt, CheckoutIntent
from scripts.billing_data_audit import collect_findings


def _now():
    return datetime.now(timezone.utc)


def _active_methods_for_user_provider(*, user_pk: int, provider: str) -> list[BillingMethod]:
    return (
        BillingMethod.query.filter(BillingMethod.user_pk == int(user_pk))
        .filter(BillingMethod.provider == str(provider or "toss"))
        .filter(BillingMethod.status == "active")
        .order_by(BillingMethod.issued_at.desc().nullslast(), BillingMethod.id.desc())
        .all()
    )


def _all_methods_for_user_provider(*, user_pk: int, provider: str) -> list[BillingMethod]:
    return (
        BillingMethod.query.filter(BillingMethod.user_pk == int(user_pk))
        .filter(BillingMethod.provider == str(provider or "toss"))
        .order_by(BillingMethod.issued_at.desc().nullslast(), BillingMethod.id.desc())
        .all()
    )


def run_recovery(*, user_pk: int | None = None, apply_changes: bool = False, limit: int = 500) -> dict[str, Any]:
    uid = int(user_pk or 0)
    target_user = uid if uid > 0 else None

    fixed_actions: list[dict[str, Any]] = []
    manual_reviews: list[dict[str, Any]] = []

    # 1) 단일 method + inactive만 남은 경우 active 복구
    attempts_q = BillingMethodRegistrationAttempt.query.filter(
        BillingMethodRegistrationAttempt.status.in_(["billing_key_issued", "completed"])
    ).order_by(BillingMethodRegistrationAttempt.id.desc())
    if target_user:
        attempts_q = attempts_q.filter(BillingMethodRegistrationAttempt.user_pk == target_user)
    attempts = attempts_q.limit(max(1, int(limit))).all()

    seen_pairs: set[tuple[int, str]] = set()
    for attempt in attempts:
        user_id = int(attempt.user_pk)
        provider = str(attempt.provider or "toss")
        key = (user_id, provider)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        active_methods = _active_methods_for_user_provider(user_pk=user_id, provider=provider)
        if active_methods:
            continue

        all_methods = _all_methods_for_user_provider(user_pk=user_id, provider=provider)
        if len(all_methods) != 1:
            manual_reviews.append(
                {
                    "reason": "inactive_only_but_candidate_count_not_one",
                    "user_pk": user_id,
                    "provider": provider,
                    "method_count": len(all_methods),
                    "attempt_id": int(attempt.id),
                }
            )
            continue

        candidate = all_methods[0]
        status = str(candidate.status or "").strip().lower()
        if status not in {"inactive"}:
            manual_reviews.append(
                {
                    "reason": "single_method_not_inactive",
                    "user_pk": user_id,
                    "provider": provider,
                    "method_id": int(candidate.id),
                    "method_status": status,
                    "attempt_id": int(attempt.id),
                }
            )
            continue
        if candidate.revoked_at is not None:
            manual_reviews.append(
                {
                    "reason": "single_method_revoked",
                    "user_pk": user_id,
                    "provider": provider,
                    "method_id": int(candidate.id),
                    "attempt_id": int(attempt.id),
                }
            )
            continue

        action = {
            "action": "activate_single_inactive_method",
            "user_pk": user_id,
            "provider": provider,
            "billing_method_id": int(candidate.id),
            "attempt_id": int(attempt.id),
        }
        if apply_changes:
            candidate.status = "active"
            candidate.updated_at = _now()
            db.session.add(candidate)
        fixed_actions.append(action)

    # 2) ready intent + billing_method_id null + 유일 active method 1개면 연결
    intents_q = CheckoutIntent.query.filter(CheckoutIntent.status == "ready_for_charge").filter(CheckoutIntent.billing_method_id.is_(None))
    if target_user:
        intents_q = intents_q.filter(CheckoutIntent.user_pk == target_user)
    intents = intents_q.order_by(CheckoutIntent.id.desc()).limit(max(1, int(limit))).all()

    for intent in intents:
        user_id = int(intent.user_pk)
        active_methods = (
            BillingMethod.query.filter(BillingMethod.user_pk == user_id)
            .filter(BillingMethod.status == "active")
            .filter(BillingMethod.revoked_at.is_(None))
            .order_by(BillingMethod.issued_at.desc().nullslast(), BillingMethod.id.desc())
            .all()
        )
        if len(active_methods) == 1:
            chosen = active_methods[0]
            action = {
                "action": "bind_ready_intent_to_single_active_method",
                "user_pk": user_id,
                "checkout_intent_id": int(intent.id),
                "billing_method_id": int(chosen.id),
            }
            if apply_changes:
                intent.billing_method_id = int(chosen.id)
                intent.updated_at = _now()
                db.session.add(intent)
            fixed_actions.append(action)
            continue

        manual_reviews.append(
            {
                "reason": "ready_intent_null_method_but_active_candidates_not_one",
                "user_pk": user_id,
                "checkout_intent_id": int(intent.id),
                "active_candidate_count": len(active_methods),
            }
        )

    if apply_changes:
        db.session.commit()
    else:
        db.session.rollback()

    # 복구 후 재진단
    remaining = collect_findings(user_pk=target_user, limit=limit)
    remaining_manual_reasons = {
        "ready_intent_unusable_method",
        "multi_active_methods",
        "attempt_intent_link_mismatch",
        "registration_active_gap",
    }
    for item in remaining:
        if item.category not in remaining_manual_reasons:
            continue
        manual_reviews.append(
            {
                "reason": f"manual_review_required:{item.reason}",
                "user_pk": item.user_pk,
                "checkout_intent_id": item.checkout_intent_id,
                "checkout_billing_method_id": item.checkout_billing_method_id,
                "billing_method_id": item.billing_method_id,
                "billing_method_status": item.billing_method_status,
                "registration_attempt_id": item.registration_attempt_id,
                "payment_attempt_id": item.payment_attempt_id,
            }
        )
    remaining_summary: dict[str, int] = {}
    for item in remaining:
        remaining_summary[item.category] = remaining_summary.get(item.category, 0) + 1

    return {
        "mode": "apply" if apply_changes else "dry_run",
        "fixed_count": len(fixed_actions),
        "manual_review_count": len(manual_reviews),
        "fixed_actions": fixed_actions,
        "manual_reviews": manual_reviews,
        "remaining_findings": len(remaining),
        "remaining_summary": remaining_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="billing 오염 데이터 보수 복구")
    parser.add_argument("--user-pk", type=int, default=0, help="특정 user만 복구")
    parser.add_argument("--limit", type=int, default=500, help="카테고리별 최대 조회")
    parser.add_argument("--apply", action="store_true", help="실제 반영 모드(기본 dry-run)")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = run_recovery(
            user_pk=(args.user_pk or None),
            apply_changes=bool(args.apply),
            limit=args.limit,
        )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=== billing data recovery ===")
        print(json.dumps({k: result[k] for k in ["mode", "fixed_count", "manual_review_count", "remaining_findings", "remaining_summary"]}, ensure_ascii=False, sort_keys=True))
        for row in result["fixed_actions"]:
            print(json.dumps(row, ensure_ascii=False))
        for row in result["manual_reviews"]:
            print(json.dumps(row, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
