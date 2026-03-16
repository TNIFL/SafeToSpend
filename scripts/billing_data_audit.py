from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app import create_app
from domain.models import (
    BillingMethod,
    BillingMethodRegistrationAttempt,
    CheckoutIntent,
    PaymentAttempt,
)


@dataclass
class AuditFinding:
    category: str
    reason: str
    user_pk: int | None = None
    registration_attempt_id: int | None = None
    registration_status: str | None = None
    checkout_intent_id: int | None = None
    checkout_status: str | None = None
    checkout_billing_method_id: int | None = None
    billing_method_id: int | None = None
    billing_method_status: str | None = None
    billing_method_issued_at: str | None = None
    billing_method_revoked_at: str | None = None
    payment_attempt_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "reason": self.reason,
            "user_pk": self.user_pk,
            "registration_attempt_id": self.registration_attempt_id,
            "registration_status": self.registration_status,
            "checkout_intent_id": self.checkout_intent_id,
            "checkout_status": self.checkout_status,
            "checkout_billing_method_id": self.checkout_billing_method_id,
            "billing_method_id": self.billing_method_id,
            "billing_method_status": self.billing_method_status,
            "billing_method_issued_at": self.billing_method_issued_at,
            "billing_method_revoked_at": self.billing_method_revoked_at,
            "payment_attempt_id": self.payment_attempt_id,
        }


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def _user_filter(value: int | None):
    uid = int(value or 0)
    return uid if uid > 0 else None


def collect_findings(*, user_pk: int | None = None, limit: int = 500) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    uid = _user_filter(user_pk)
    max_rows = max(1, int(limit or 500))

    # 1) registration_attempt는 billing_key_issued/완료인데 active method가 없는 경우
    attempts_q = BillingMethodRegistrationAttempt.query.order_by(BillingMethodRegistrationAttempt.id.desc())
    if uid:
        attempts_q = attempts_q.filter(BillingMethodRegistrationAttempt.user_pk == uid)
    attempts = attempts_q.limit(max_rows).all()
    for attempt in attempts:
        status = str(attempt.status or "").strip().lower()
        if status not in {"billing_key_issued", "completed"}:
            continue
        active_count = (
            BillingMethod.query.filter(BillingMethod.user_pk == int(attempt.user_pk))
            .filter(BillingMethod.provider == str(attempt.provider or "toss"))
            .filter(BillingMethod.status == "active")
            .count()
        )
        if active_count > 0:
            continue
        latest_method = (
            BillingMethod.query.filter(BillingMethod.user_pk == int(attempt.user_pk))
            .filter(BillingMethod.provider == str(attempt.provider or "toss"))
            .order_by(BillingMethod.issued_at.desc().nullslast(), BillingMethod.id.desc())
            .first()
        )
        findings.append(
            AuditFinding(
                category="registration_active_gap",
                reason="registration_attempt_completed_but_no_active_billing_method",
                user_pk=int(attempt.user_pk),
                registration_attempt_id=int(attempt.id),
                registration_status=str(attempt.status or ""),
                billing_method_id=(int(latest_method.id) if latest_method else None),
                billing_method_status=(str(latest_method.status or "") if latest_method else None),
                billing_method_issued_at=_iso(getattr(latest_method, "issued_at", None)),
                billing_method_revoked_at=_iso(getattr(latest_method, "revoked_at", None)),
            )
        )

    # 2) ready_for_charge intent인데 billing_method가 unusable
    intents_q = CheckoutIntent.query.filter(CheckoutIntent.status == "ready_for_charge").order_by(CheckoutIntent.id.desc())
    if uid:
        intents_q = intents_q.filter(CheckoutIntent.user_pk == uid)
    intents = intents_q.limit(max_rows).all()
    for intent in intents:
        reason = None
        method = None
        method_id = int(intent.billing_method_id or 0)
        if method_id <= 0:
            reason = "ready_intent_without_billing_method_id"
        else:
            method = BillingMethod.query.filter(BillingMethod.id == method_id).first()
            if not method:
                reason = "ready_intent_billing_method_missing"
            elif int(method.user_pk or 0) != int(intent.user_pk or 0):
                reason = "ready_intent_billing_method_owner_mismatch"
            elif str(method.status or "").strip().lower() != "active":
                reason = "ready_intent_billing_method_not_active"
            elif getattr(method, "revoked_at", None) is not None:
                reason = "ready_intent_billing_method_revoked"
        if not reason:
            continue
        findings.append(
            AuditFinding(
                category="ready_intent_unusable_method",
                reason=reason,
                user_pk=int(intent.user_pk),
                checkout_intent_id=int(intent.id),
                checkout_status=str(intent.status or ""),
                checkout_billing_method_id=(int(intent.billing_method_id) if intent.billing_method_id else None),
                billing_method_id=(int(method.id) if method else None),
                billing_method_status=(str(method.status or "") if method else None),
                billing_method_issued_at=_iso(getattr(method, "issued_at", None)),
                billing_method_revoked_at=_iso(getattr(method, "revoked_at", None)),
            )
        )

    # 3) 같은 user/provider active method가 2개 이상
    multi_q = BillingMethod.query.filter(BillingMethod.status == "active")
    if uid:
        multi_q = multi_q.filter(BillingMethod.user_pk == uid)
    rows = multi_q.order_by(BillingMethod.user_pk.asc(), BillingMethod.provider.asc(), BillingMethod.id.desc()).all()
    grouped: dict[tuple[int, str], list[BillingMethod]] = {}
    for row in rows:
        key = (int(row.user_pk), str(row.provider or "toss"))
        grouped.setdefault(key, []).append(row)
    for (user_id, _provider), methods in grouped.items():
        if len(methods) < 2:
            continue
        latest = methods[0]
        findings.append(
            AuditFinding(
                category="multi_active_methods",
                reason=f"active_billing_method_count={len(methods)}",
                user_pk=user_id,
                billing_method_id=int(latest.id),
                billing_method_status=str(latest.status or ""),
                billing_method_issued_at=_iso(latest.issued_at),
                billing_method_revoked_at=_iso(latest.revoked_at),
            )
        )

    # 4) payment_attempt / intent 연결 어긋남
    pa_q = PaymentAttempt.query.filter(PaymentAttempt.checkout_intent_id.isnot(None)).order_by(PaymentAttempt.id.desc())
    if uid:
        pa_q = pa_q.filter(PaymentAttempt.user_pk == uid)
    attempts = pa_q.limit(max_rows).all()
    for pa in attempts:
        intent_id = int(pa.checkout_intent_id or 0)
        intent = CheckoutIntent.query.filter_by(id=intent_id).first()
        if not intent:
            findings.append(
                AuditFinding(
                    category="attempt_intent_link_mismatch",
                    reason="payment_attempt_checkout_intent_missing",
                    user_pk=int(pa.user_pk),
                    payment_attempt_id=int(pa.id),
                    checkout_intent_id=intent_id,
                )
            )
            continue
        if int(intent.user_pk or 0) != int(pa.user_pk or 0):
            findings.append(
                AuditFinding(
                    category="attempt_intent_link_mismatch",
                    reason="payment_attempt_checkout_intent_user_mismatch",
                    user_pk=int(pa.user_pk),
                    payment_attempt_id=int(pa.id),
                    checkout_intent_id=int(intent.id),
                    checkout_status=str(intent.status or ""),
                    checkout_billing_method_id=(int(intent.billing_method_id) if intent.billing_method_id else None),
                )
            )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="billing 오염 데이터 진단")
    parser.add_argument("--user-pk", type=int, default=0, help="특정 user만 점검")
    parser.add_argument("--limit", type=int, default=500, help="카테고리별 최대 조회 건수")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        findings = collect_findings(user_pk=(args.user_pk or None), limit=args.limit)

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        summary: dict[str, int] = {}
        for f in findings:
            summary[f.category] = summary.get(f.category, 0) + 1
        print("=== billing data audit summary ===")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        print(f"total_findings={len(findings)}")
        for row in findings:
            print(json.dumps(row.to_dict(), ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
