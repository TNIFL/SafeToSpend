from __future__ import annotations

import importlib
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

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
from services.billing.service import (
    complete_registration_success_by_order,
    get_or_create_billing_customer,
    ingest_payment_event,
    mark_registration_failed_by_order,
    start_registration_attempt,
)
from services.billing.projector import apply_entitlement_from_payment_attempt
from services.billing.reconcile import reconcile_by_order_id, reconcile_from_payment_event


class _DummyCipher:
    def encrypt(self, plain_text: str) -> str:
        return f"enc::{plain_text}"

    def decrypt(self, cipher_text: str) -> str:
        return str(cipher_text or "").replace("enc::", "", 1)


class BillingConcurrencyPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = (
            str(os.getenv("BILLING_PG_TEST_DSN") or "").strip()
            or str(os.getenv("SQLALCHEMY_DATABASE_URI") or "").strip()
            or str(os.getenv("DATABASE_URL") or "").strip()
        )
        if not dsn.lower().startswith("postgresql"):
            raise unittest.SkipTest("Postgres DSN이 없어 동시성 검증을 건너뜁니다.")

        engine = sa.create_engine(dsn)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception as e:  # pragma: no cover - env dependent
            raise unittest.SkipTest(f"Postgres 연결 불가: {type(e).__name__}") from e
        finally:
            engine.dispose()

        cls._env_backup = {
            "SQLALCHEMY_DATABASE_URI": os.getenv("SQLALCHEMY_DATABASE_URI"),
            "DATABASE_URL": os.getenv("DATABASE_URL"),
            "APP_ENV": os.getenv("APP_ENV"),
            "BILLING_GUARD_MODE": os.getenv("BILLING_GUARD_MODE"),
        }
        os.environ["SQLALCHEMY_DATABASE_URI"] = dsn
        os.environ["DATABASE_URL"] = dsn
        os.environ["APP_ENV"] = "development"
        os.environ["BILLING_GUARD_MODE"] = "warn"

        app_module = importlib.import_module("app")
        cls.app = app_module.create_app()

        with cls.app.app_context():
            inspector = sa.inspect(db.engine)
            table_names = set(inspector.get_table_names())
            required = {
                "users",
                "billing_customers",
                "billing_methods",
                "billing_method_registration_attempts",
                "billing_subscriptions",
                "billing_subscription_items",
                "billing_payment_attempts",
                "billing_payment_events",
                "entitlement_change_logs",
            }
            missing = sorted(required - table_names)
            if missing:
                raise unittest.SkipTest(f"필수 테이블 누락: {', '.join(missing)}")

    @classmethod
    def tearDownClass(cls) -> None:
        backup = getattr(cls, "_env_backup", {})
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self) -> None:
        self.user_id = self._create_user()

    def tearDown(self) -> None:
        self._cleanup_user(self.user_id)

    def _create_user(self) -> int:
        with self.app.app_context():
            email = f"billing-qa-{uuid.uuid4().hex[:12]}@example.com"
            user = User(
                email=email,
                password_hash="qa-hash",
                plan="free",
                plan_code="free",
                plan_status="active",
                extra_account_slots=0,
                plan_updated_at=datetime.now(timezone.utc),
            )
            db.session.add(user)
            db.session.commit()
            return int(user.id)

    def _cleanup_user(self, user_id: int) -> None:
        if not user_id:
            return
        with self.app.app_context():
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

    def _run_in_parallel(self, workers: list):
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(fn) for fn in workers]
            return [f.result(timeout=20) for f in futures]

    def test_concurrent_registration_success_same_order(self) -> None:
        with self.app.app_context():
            attempt = start_registration_attempt(user_pk=self.user_id)
            order_id = str(attempt.order_id)
            customer_key = str(attempt.customer_key)

        counter = {"calls": 0}
        counter_lock = threading.Lock()
        barrier = threading.Barrier(2)
        billing_key = f"bk-race-{uuid.uuid4().hex}"

        def _exchange_auth_key(**_kwargs):
            with counter_lock:
                counter["calls"] += 1
            return {"billing_key": billing_key}

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return complete_registration_success_by_order(
                    order_id=order_id,
                    auth_key="auth-key-for-race",
                    customer_key=customer_key,
                    exchange_auth_key_fn=_exchange_auth_key,
                    key_cipher=_DummyCipher(),
                    encryption_key_version="v1",
                )

        results = self._run_in_parallel([_worker, _worker])
        self.assertEqual(counter["calls"], 1)
        self.assertTrue(all(bool(r.get("ok")) for r in results))

        with self.app.app_context():
            row = BillingMethodRegistrationAttempt.query.filter_by(order_id=order_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(str(row.status), "billing_key_issued")
            active_methods = BillingMethod.query.filter_by(user_pk=self.user_id, status="active").count()
            self.assertEqual(active_methods, 1)

    def test_success_and_fail_race_same_order(self) -> None:
        with self.app.app_context():
            attempt = start_registration_attempt(user_pk=self.user_id)
            order_id = str(attempt.order_id)
            customer_key = str(attempt.customer_key)

        barrier = threading.Barrier(2)
        billing_key = f"bk-race-{uuid.uuid4().hex}"

        def _exchange_auth_key(**_kwargs):
            return {"billing_key": billing_key}

        def _success_worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return complete_registration_success_by_order(
                    order_id=order_id,
                    auth_key="auth-key-for-race",
                    customer_key=customer_key,
                    exchange_auth_key_fn=_exchange_auth_key,
                    key_cipher=_DummyCipher(),
                    encryption_key_version="v1",
                )

        def _fail_worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return mark_registration_failed_by_order(
                    order_id=order_id,
                    fail_code="forced_fail",
                    fail_message="race fail",
                )

        self._run_in_parallel([_success_worker, _fail_worker])

        with self.app.app_context():
            row = BillingMethodRegistrationAttempt.query.filter_by(order_id=order_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(str(row.status), "billing_key_issued")
            self.assertEqual(BillingMethod.query.filter_by(user_pk=self.user_id, status="active").count(), 1)

    def test_duplicate_webhook_same_transmission_id(self) -> None:
        tx_id = f"tx-{uuid.uuid4().hex[:16]}"
        order_id = f"ord_{uuid.uuid4().hex[:16]}"
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "orderId": order_id, "paymentKey": f"pay_{uuid.uuid4().hex[:20]}"}
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return ingest_payment_event(
                    payload=payload,
                    transmission_id=tx_id,
                    user_pk=self.user_id,
                )

        results = self._run_in_parallel([_worker, _worker])
        duplicate_flags = sorted(bool(r.get("duplicate")) for r in results)
        self.assertEqual(duplicate_flags, [False, True])

        with self.app.app_context():
            count = PaymentEvent.query.filter_by(provider="toss", transmission_id=tx_id).count()
            self.assertEqual(count, 1)

    def test_duplicate_webhook_same_event_hash_without_transmission(self) -> None:
        order_id = f"ord_{uuid.uuid4().hex[:16]}"
        payload = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "orderId": order_id,
            "paymentKey": f"pay_{uuid.uuid4().hex[:20]}",
            "status": "DONE",
        }
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return ingest_payment_event(
                    payload=payload,
                    transmission_id=None,
                    user_pk=self.user_id,
                )

        results = self._run_in_parallel([_worker, _worker])
        duplicate_flags = sorted(bool(r.get("duplicate")) for r in results)
        self.assertEqual(duplicate_flags, [False, True])

        with self.app.app_context():
            count = PaymentEvent.query.filter_by(provider="toss", related_order_id=order_id).count()
            self.assertEqual(count, 1)

    def test_concurrent_entitlement_change_log_same_source(self) -> None:
        source_id = f"src-{uuid.uuid4().hex[:12]}"
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                row = EntitlementChangeLog(
                    user_pk=self.user_id,
                    source_type="concurrency_test",
                    source_id=source_id,
                    before_json={"plan_code": "free", "plan_status": "active", "extra_account_slots": 0},
                    after_json={"plan_code": "basic", "plan_status": "active", "extra_account_slots": 0},
                    reason="race test",
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

        results = self._run_in_parallel([_worker, _worker])
        self.assertEqual(sorted(results), ["applied", "duplicate"])

        with self.app.app_context():
            count = EntitlementChangeLog.query.filter_by(
                user_pk=self.user_id,
                source_type="concurrency_test",
                source_id=source_id,
            ).count()
            self.assertEqual(count, 1)

    def _create_reconcile_fixture(self) -> dict[str, int | str]:
        with self.app.app_context():
            now = datetime.now(timezone.utc)
            customer = get_or_create_billing_customer(user_pk=self.user_id, provider="toss")
            sub = Subscription(
                user_pk=self.user_id,
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
                user_pk=self.user_id,
                item_type="plan_base",
                item_code="basic",
                quantity=1,
                unit_price_krw=6900,
                amount_krw=6900,
                status="active",
                effective_from=now - timedelta(hours=1),
                effective_to=None,
                snapshot_json={},
                created_at=now,
                updated_at=now,
            )
            db.session.add(item)
            order_id = f"ord_{uuid.uuid4().hex[:20]}"
            attempt = PaymentAttempt(
                user_pk=self.user_id,
                subscription_id=int(sub.id),
                provider="toss",
                attempt_type="initial",
                order_id=order_id,
                payment_key=None,
                amount_krw=6900,
                currency="KRW",
                status="charge_started",
                requested_at=now,
                created_at=now,
                updated_at=now,
            )
            db.session.add(attempt)
            db.session.commit()
            return {
                "subscription_id": int(sub.id),
                "payment_attempt_id": int(attempt.id),
                "order_id": order_id,
            }

    def test_concurrent_reconcile_same_order_projects_once(self) -> None:
        fixture = self._create_reconcile_fixture()
        order_id = str(fixture["order_id"])
        payment_key = f"pay_{uuid.uuid4().hex[:18]}"
        barrier = threading.Barrier(2)

        def _provider_lookup(**_kwargs):
            return {
                "provider_status": "done",
                "order_id": order_id,
                "payment_key": payment_key,
                "total_amount": 6900,
                "currency": "KRW",
            }

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return reconcile_by_order_id(
                    order_id=order_id,
                    provider_lookup_fn=_provider_lookup,
                    apply_projection=True,
                    commit=True,
                )

        results = self._run_in_parallel([_worker, _worker])
        self.assertTrue(all(bool(r.get("status_after")) for r in results))

        with self.app.app_context():
            attempt = PaymentAttempt.query.filter_by(order_id=order_id).first()
            self.assertIsNotNone(attempt)
            self.assertEqual(str(attempt.status), "reconciled")
            user = User.query.filter_by(id=self.user_id).first()
            self.assertIsNotNone(user)
            self.assertEqual(str(user.plan_code), "basic")
            self.assertEqual(int(user.extra_account_slots or 0), 0)
            logs = (
                EntitlementChangeLog.query.filter_by(user_pk=self.user_id, source_type="reconcile_projection")
                .filter(EntitlementChangeLog.source_id.like(f"attempt:{int(fixture['payment_attempt_id'])}%"))
                .count()
            )
            self.assertEqual(logs, 1)

    def test_reconcile_webhook_and_order_race_is_idempotent(self) -> None:
        fixture = self._create_reconcile_fixture()
        order_id = str(fixture["order_id"])
        payment_key = f"pay_{uuid.uuid4().hex[:18]}"

        with self.app.app_context():
            now = datetime.now(timezone.utc)
            event = PaymentEvent(
                user_pk=self.user_id,
                provider="toss",
                event_type="PAYMENT_STATUS_CHANGED",
                status="received",
                transmission_id=f"tx_{uuid.uuid4().hex[:16]}",
                event_hash=uuid.uuid4().hex,
                related_order_id=order_id,
                related_payment_key=None,
                payload_json={
                    "status": "done",
                    "order_id": order_id,
                    "payment_key": payment_key,
                    "total_amount": 6900,
                    "currency": "KRW",
                },
                received_at=now,
                created_at=now,
                updated_at=now,
            )
            db.session.add(event)
            db.session.commit()
            event_id = int(event.id)

        barrier = threading.Barrier(2)

        def _provider_lookup(**_kwargs):
            return {
                "provider_status": "done",
                "order_id": order_id,
                "payment_key": payment_key,
                "total_amount": 6900,
                "currency": "KRW",
            }

        def _worker_order():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return reconcile_by_order_id(
                    order_id=order_id,
                    provider_lookup_fn=_provider_lookup,
                    apply_projection=True,
                    commit=True,
                )

        def _worker_event():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return reconcile_from_payment_event(
                    payment_event_id=event_id,
                    provider_lookup_fn=_provider_lookup,
                    apply_projection=True,
                    commit=True,
                )

        self._run_in_parallel([_worker_order, _worker_event])

        with self.app.app_context():
            attempt = PaymentAttempt.query.filter_by(order_id=order_id).first()
            self.assertIsNotNone(attempt)
            self.assertEqual(str(attempt.status), "reconciled")
            event = PaymentEvent.query.filter_by(id=event_id).first()
            self.assertIsNotNone(event)
            self.assertIn(str(event.status), {"applied", "validated"})
            logs = (
                EntitlementChangeLog.query.filter_by(user_pk=self.user_id, source_type="reconcile_projection")
                .filter(EntitlementChangeLog.source_id.like(f"attempt:{int(fixture['payment_attempt_id'])}%"))
                .count()
            )
            self.assertEqual(logs, 1)

    def test_concurrent_projector_same_source_id_is_noop_for_second(self) -> None:
        fixture = self._create_reconcile_fixture()
        attempt_id = int(fixture["payment_attempt_id"])
        with self.app.app_context():
            attempt = PaymentAttempt.query.filter_by(id=attempt_id).first()
            self.assertIsNotNone(attempt)
            attempt.status = "reconciled"
            attempt.reconciled_at = datetime.now(timezone.utc)
            db.session.add(attempt)
            db.session.commit()

        source_id = f"fixed-{uuid.uuid4().hex[:8]}"
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait(timeout=5)
            with self.app.app_context():
                return apply_entitlement_from_payment_attempt(
                    payment_attempt_id=attempt_id,
                    source_type="projector_race",
                    source_id=source_id,
                    reason="race",
                    commit=True,
                )

        results = self._run_in_parallel([_worker, _worker])
        applied_flags = sorted(bool(r.get("applied")) for r in results)
        self.assertEqual(applied_flags, [False, True])

        with self.app.app_context():
            count = EntitlementChangeLog.query.filter_by(
                user_pk=self.user_id,
                source_type="projector_race",
                source_id=source_id,
            ).count()
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
