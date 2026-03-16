from __future__ import annotations

import argparse
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app import create_app
from core.extensions import db
from domain.models import (
    BillingCustomer,
    BillingMethod,
    BillingMethodRegistrationAttempt,
    EntitlementChangeLog,
    PaymentAttempt,
    PaymentEvent,
    Subscription,
    SubscriptionItem,
    User,
)
from services.billing.projector import apply_entitlement_from_payment_attempt
from services.billing.reconcile import reconcile_by_order_id, reconcile_from_payment_event
from services.billing.service import (
    complete_registration_success_by_order,
    get_or_create_billing_customer,
    ingest_payment_event,
    mark_registration_failed_by_order,
    start_registration_attempt,
)


class _DummyCipher:
    def encrypt(self, plain_text: str) -> str:
        return f"enc::{plain_text}"

    def decrypt(self, cipher_text: str) -> str:
        return str(cipher_text or "").replace("enc::", "", 1)


def _run_parallel(workers: list):
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futures = [pool.submit(fn) for fn in workers]
        return [f.result(timeout=20) for f in futures]


def _cleanup_user(user_id: int) -> None:
    db.session.query(EntitlementChangeLog).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(PaymentEvent).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(PaymentAttempt).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(SubscriptionItem).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(Subscription).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(BillingMethodRegistrationAttempt).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(BillingMethod).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(BillingCustomer).filter_by(user_pk=int(user_id)).delete(synchronize_session=False)
    db.session.query(User).filter_by(id=int(user_id)).delete(synchronize_session=False)
    db.session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Postgres billing concurrency probe")
    parser.add_argument("--cleanup", action="store_true", help="검증 후 생성한 사용자/데이터 정리")
    args = parser.parse_args()

    app = create_app()
    report: dict[str, dict[str, object]] = {}

    with app.app_context():
        user = User(
            email=f"billing-probe-{uuid.uuid4().hex[:12]}@example.com",
            password_hash="probe-hash",
            plan="free",
            plan_code="free",
            plan_status="active",
            extra_account_slots=0,
            plan_updated_at=datetime.now(timezone.utc),
        )
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    try:
        with app.app_context():
            attempt = start_registration_attempt(user_pk=user_id)
            order_id = str(attempt.order_id)
            customer_key = str(attempt.customer_key)

        counter = {"calls": 0}
        counter_lock = threading.Lock()
        barrier = threading.Barrier(2)
        billing_key = f"bk-probe-{uuid.uuid4().hex}"

        def _exchange_auth_key(**_kwargs):
            with counter_lock:
                counter["calls"] += 1
            return {"billing_key": billing_key}

        def _success_worker():
            barrier.wait(timeout=5)
            with app.app_context():
                return complete_registration_success_by_order(
                    order_id=order_id,
                    auth_key="auth-probe",
                    customer_key=customer_key,
                    exchange_auth_key_fn=_exchange_auth_key,
                    key_cipher=_DummyCipher(),
                    encryption_key_version="v1",
                )

        results = _run_parallel([_success_worker, _success_worker])
        report["registration_success_race"] = {
            "ok": bool(all(bool(r.get("ok")) for r in results) and counter["calls"] == 1),
            "exchange_calls": int(counter["calls"]),
        }

        tx_id = f"tx-{uuid.uuid4().hex[:16]}"
        order_id2 = f"ord_{uuid.uuid4().hex[:16]}"
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": order_id2, "paymentKey": f"pay_{uuid.uuid4().hex[:20]}"}
        barrier2 = threading.Barrier(2)

        def _webhook_worker():
            barrier2.wait(timeout=5)
            with app.app_context():
                return ingest_payment_event(payload=payload, transmission_id=tx_id, user_pk=user_id)

        webhook_results = _run_parallel([_webhook_worker, _webhook_worker])
        dup_flags = sorted(bool(r.get("duplicate")) for r in webhook_results)
        report["webhook_transmission_duplicate"] = {
            "ok": dup_flags == [False, True],
            "duplicate_flags": dup_flags,
        }

        src_id = f"src-{uuid.uuid4().hex[:12]}"
        barrier3 = threading.Barrier(2)

        def _ent_worker():
            barrier3.wait(timeout=5)
            with app.app_context():
                row = EntitlementChangeLog(
                    user_pk=user_id,
                    source_type="probe",
                    source_id=src_id,
                    before_json={"plan_code": "free", "plan_status": "active", "extra_account_slots": 0},
                    after_json={"plan_code": "basic", "plan_status": "active", "extra_account_slots": 0},
                    reason="probe",
                    applied_at=datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
                )
                db.session.add(row)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    return "duplicate"
                return "applied"

        ent_results = sorted(_run_parallel([_ent_worker, _ent_worker]))
        report["entitlement_log_duplicate"] = {
            "ok": ent_results == ["applied", "duplicate"],
            "results": ent_results,
        }

        # success/fail 경합도 확인
        with app.app_context():
            attempt2 = start_registration_attempt(user_pk=user_id)
            order_id3 = str(attempt2.order_id)
            customer_key3 = str(attempt2.customer_key)

        barrier4 = threading.Barrier(2)
        billing_key2 = f"bk-probe-{uuid.uuid4().hex}"

        def _exchange2(**_kwargs):
            return {"billing_key": billing_key2}

        def _worker_success2():
            barrier4.wait(timeout=5)
            with app.app_context():
                return complete_registration_success_by_order(
                    order_id=order_id3,
                    auth_key="auth-probe-2",
                    customer_key=customer_key3,
                    exchange_auth_key_fn=_exchange2,
                    key_cipher=_DummyCipher(),
                    encryption_key_version="v1",
                )

        def _worker_fail2():
            barrier4.wait(timeout=5)
            with app.app_context():
                return mark_registration_failed_by_order(order_id=order_id3, fail_code="forced", fail_message="probe")

        _run_parallel([_worker_success2, _worker_fail2])
        with app.app_context():
            row = BillingMethodRegistrationAttempt.query.filter_by(order_id=order_id3).first()
            status = str(row.status or "") if row else "missing"
        report["registration_success_fail_race"] = {"ok": status == "billing_key_issued", "final_status": status}

        # reconcile/projector 동시성 probe
        with app.app_context():
            now = datetime.now(timezone.utc)
            customer = get_or_create_billing_customer(user_pk=user_id, provider="toss")
            sub = Subscription(
                user_pk=user_id,
                provider="toss",
                billing_customer_id=int(customer.id),
                status="pending_activation",
                billing_anchor_at=now,
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                next_billing_at=now + timedelta(days=30),
                created_at=now,
                updated_at=now,
            )
            db.session.add(sub)
            db.session.flush()
            item = SubscriptionItem(
                subscription_id=int(sub.id),
                user_pk=user_id,
                item_type="plan_base",
                item_code="basic",
                quantity=1,
                unit_price_krw=6900,
                amount_krw=6900,
                status="active",
                effective_from=now,
                effective_to=None,
                snapshot_json={},
                created_at=now,
                updated_at=now,
            )
            db.session.add(item)
            order_id4 = f"ord_{uuid.uuid4().hex[:20]}"
            attempt4 = PaymentAttempt(
                user_pk=user_id,
                subscription_id=int(sub.id),
                provider="toss",
                attempt_type="initial",
                order_id=order_id4,
                payment_key=None,
                amount_krw=6900,
                currency="KRW",
                status="charge_started",
                requested_at=now,
                created_at=now,
                updated_at=now,
            )
            db.session.add(attempt4)
            db.session.commit()
            attempt4_id = int(attempt4.id)

        payment_key4 = f"pay_{uuid.uuid4().hex[:18]}"
        barrier5 = threading.Barrier(2)

        def _provider_lookup(**_kwargs):
            return {
                "provider_status": "done",
                "order_id": order_id4,
                "payment_key": payment_key4,
                "total_amount": 6900,
                "currency": "KRW",
            }

        def _reconcile_worker():
            barrier5.wait(timeout=5)
            with app.app_context():
                return reconcile_by_order_id(
                    order_id=order_id4,
                    provider_lookup_fn=_provider_lookup,
                    apply_projection=True,
                    commit=True,
                )

        rec_results = _run_parallel([_reconcile_worker, _reconcile_worker])
        with app.app_context():
            attempt_row = PaymentAttempt.query.filter_by(id=attempt4_id).first()
            logs = (
                EntitlementChangeLog.query.filter_by(user_pk=user_id, source_type="reconcile_projection")
                .filter(EntitlementChangeLog.source_id.like(f"attempt:{attempt4_id}%"))
                .count()
            )
        report["reconcile_projection_race"] = {
            "ok": bool(attempt_row and str(attempt_row.status or "") == "reconciled" and int(logs) == 1),
            "result_statuses": [str(r.get("status_after") or "") for r in rec_results],
            "entitlement_logs": int(logs),
        }

        # projector 멱등성 probe
        with app.app_context():
            row = PaymentAttempt.query.filter_by(id=attempt4_id).first()
            if row:
                row.status = "reconciled"
                row.reconciled_at = datetime.now(timezone.utc)
                db.session.add(row)
                db.session.commit()
        source_id = f"probe-source-{uuid.uuid4().hex[:8]}"
        barrier6 = threading.Barrier(2)

        def _project_worker():
            barrier6.wait(timeout=5)
            with app.app_context():
                return apply_entitlement_from_payment_attempt(
                    payment_attempt_id=attempt4_id,
                    source_type="projector_probe",
                    source_id=source_id,
                    reason="probe",
                    commit=True,
                )

        proj_results = _run_parallel([_project_worker, _project_worker])
        with app.app_context():
            proj_count = EntitlementChangeLog.query.filter_by(
                user_pk=user_id,
                source_type="projector_probe",
                source_id=source_id,
            ).count()
        report["projector_source_idempotency"] = {
            "ok": int(proj_count) == 1,
            "applied_flags": sorted(bool(r.get("applied")) for r in proj_results),
            "log_count": int(proj_count),
        }

    finally:
        with app.app_context():
            if args.cleanup:
                _cleanup_user(user_id)

    ok = all(bool(v.get("ok")) for v in report.values())
    print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
