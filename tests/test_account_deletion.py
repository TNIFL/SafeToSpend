from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event, text

from core.extensions import db
from domain.models import (
    BankAccountLink,
    BillingCustomer,
    BillingMethod,
    BillingMethodRegistrationAttempt,
    CheckoutIntent,
    EntitlementChangeLog,
    EvidenceItem,
    NhisBillHistory,
    NhisUserProfile,
    OfficialDataDocument,
    PaymentAttempt,
    PaymentEvent,
    ReceiptBatch,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    ReceiptItem,
    RefreshToken,
    Settings,
    Subscription,
    SubscriptionItem,
    TaxProfile,
    Transaction,
    User,
    UserBankAccount,
)
from services.auth import delete_user_account, register_user


class AccountDeletionRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "account-delete-test"
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        self.app = app

        with self.app.app_context():
            @event.listens_for(db.engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _conn_record):  # pragma: no cover
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            self._create_schema()

    def _create_schema(self) -> None:
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        statements = [
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(120) NOT NULL UNIQUE,
                password_hash VARCHAR(256) NOT NULL,
                is_admin BOOLEAN NOT NULL DEFAULT 0,
                plan VARCHAR(16) NOT NULL DEFAULT 'free',
                plan_code VARCHAR(16) NOT NULL DEFAULT 'free',
                plan_status VARCHAR(16) NOT NULL DEFAULT 'active',
                extra_account_slots INTEGER NOT NULL DEFAULT 0,
                plan_updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE TABLE settings (user_pk INTEGER PRIMARY KEY REFERENCES users(id), default_tax_rate FLOAT NOT NULL, custom_rates TEXT NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE tax_profiles (user_pk INTEGER PRIMARY KEY REFERENCES users(id), profile_json TEXT NOT NULL DEFAULT '{}', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE import_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), error_summary TEXT)",
            "CREATE TABLE user_bank_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), account_fingerprint VARCHAR(64))",
            "CREATE TABLE bank_account_links (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), bank_account_id INTEGER REFERENCES user_bank_accounts(id), account_number VARCHAR(64))",
            "CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), import_job_id INTEGER REFERENCES import_jobs(id), bank_account_id INTEGER REFERENCES user_bank_accounts(id), occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, direction VARCHAR(8) NOT NULL DEFAULT 'out', amount_krw INTEGER NOT NULL DEFAULT 1, external_hash VARCHAR(64) NOT NULL DEFAULT 'x')",
            "CREATE TABLE evidence_items (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), transaction_id INTEGER REFERENCES transactions(id), file_key VARCHAR(512), deleted_at DATETIME)",
            "CREATE TABLE income_labels (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), transaction_id INTEGER NOT NULL REFERENCES transactions(id))",
            "CREATE TABLE expense_labels (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), transaction_id INTEGER NOT NULL REFERENCES transactions(id))",
            "CREATE TABLE receipt_batches (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE receipt_items (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), batch_id INTEGER REFERENCES receipt_batches(id), file_key VARCHAR(512))",
            "CREATE TABLE receipt_expense_followup_answers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), transaction_id INTEGER NOT NULL REFERENCES transactions(id), evidence_item_id INTEGER REFERENCES evidence_items(id), question_key VARCHAR(64), answered_by INTEGER REFERENCES users(id), updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE receipt_expense_reinforcements (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), transaction_id INTEGER NOT NULL REFERENCES transactions(id), evidence_item_id INTEGER REFERENCES evidence_items(id), supporting_file_key VARCHAR(512), updated_by INTEGER REFERENCES users(id), updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE official_data_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), raw_file_key VARCHAR(512))",
            "CREATE TABLE action_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE recurring_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE counterparty_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE counterparty_expense_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE weekly_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE dashboard_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE dashboard_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE hold_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE tax_buffer_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE user_dashboard_state (user_pk INTEGER PRIMARY KEY REFERENCES users(id), state_json TEXT NOT NULL DEFAULT '{}')",
            "CREATE TABLE recurring_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE csv_format_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE inquiries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), subject VARCHAR(255))",
            "CREATE TABLE refresh_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), token_hash VARCHAR(64), replaced_by_id INTEGER REFERENCES refresh_tokens(id), revoked_at DATETIME)",
            "CREATE TABLE nhis_user_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL UNIQUE REFERENCES users(id), member_type VARCHAR(16))",
            "CREATE TABLE nhis_bill_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), bill_year INTEGER NOT NULL, bill_month INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE asset_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL UNIQUE REFERENCES users(id))",
            "CREATE TABLE asset_items (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), kind VARCHAR(32))",
            "CREATE TABLE billing_customers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), customer_key VARCHAR(64))",
            "CREATE TABLE billing_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), billing_customer_id INTEGER NOT NULL REFERENCES billing_customers(id), billing_key_enc TEXT NOT NULL DEFAULT 'enc', billing_key_hash VARCHAR(64) NOT NULL DEFAULT 'hash')",
            "CREATE TABLE billing_method_registration_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), billing_customer_id INTEGER REFERENCES billing_customers(id), order_id VARCHAR(64) NOT NULL DEFAULT 'ord')",
            "CREATE TABLE billing_subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), billing_customer_id INTEGER NOT NULL REFERENCES billing_customers(id), billing_method_id INTEGER REFERENCES billing_methods(id))",
            "CREATE TABLE billing_subscription_items (id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id INTEGER NOT NULL REFERENCES billing_subscriptions(id), user_pk INTEGER NOT NULL REFERENCES users(id))",
            "CREATE TABLE billing_checkout_intents (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), billing_method_id INTEGER REFERENCES billing_methods(id), related_subscription_id INTEGER REFERENCES billing_subscriptions(id), resume_token VARCHAR(128))",
            "CREATE TABLE billing_payment_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), subscription_id INTEGER REFERENCES billing_subscriptions(id), checkout_intent_id INTEGER REFERENCES billing_checkout_intents(id), order_id VARCHAR(64) NOT NULL DEFAULT 'pay-ord')",
            "CREATE TABLE billing_payment_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER REFERENCES users(id), event_hash VARCHAR(64) NOT NULL DEFAULT 'evt')",
            "CREATE TABLE entitlement_change_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_pk INTEGER NOT NULL REFERENCES users(id), source_type VARCHAR(32) NOT NULL DEFAULT 'seed', source_id VARCHAR(64) NOT NULL DEFAULT '1')",
        ]
        for sql in statements:
            db.session.execute(text(sql))
        db.session.commit()

    def _seed_users(self) -> tuple[int, int]:
        ok, msg = register_user("owner@example.com", "Password123!")
        self.assertTrue(ok, msg)
        ok, msg = register_user("other@example.com", "Password123!")
        self.assertTrue(ok, msg)
        owner = User.query.filter_by(email="owner@example.com").first()
        other = User.query.filter_by(email="other@example.com").first()
        self.assertIsNotNone(owner)
        self.assertIsNotNone(other)
        return int(owner.id), int(other.id)

    def _count_for_user(self, table_name: str, user_pk: int) -> int:
        return int(
            db.session.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE user_pk = :uid"),
                {"uid": user_pk},
            ).scalar()
            or 0
        )

    def test_delete_user_account_purges_owned_rows_and_file_keys(self) -> None:
        with self.app.app_context():
            owner_id, other_id = self._seed_users()

            inserts = [
                ("INSERT INTO import_jobs (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO import_jobs (id, user_pk) VALUES (2, :uid)", {"uid": other_id}),
                ("INSERT INTO user_bank_accounts (id, user_pk, account_fingerprint) VALUES (1, :uid, 'fp-1')", {"uid": owner_id}),
                ("INSERT INTO bank_account_links (id, user_pk, bank_account_id, account_number) VALUES (1, :uid, 1, 'acct-token')", {"uid": owner_id}),
                (
                    "INSERT INTO transactions (id, user_pk, import_job_id, bank_account_id, occurred_at, direction, amount_krw, external_hash) "
                    "VALUES (1, :uid, 1, 1, CURRENT_TIMESTAMP, 'out', 1000, 'tx-hash-1')",
                    {"uid": owner_id},
                ),
                (
                    "INSERT INTO transactions (id, user_pk, import_job_id, occurred_at, direction, amount_krw, external_hash) "
                    "VALUES (2, :uid, 2, CURRENT_TIMESTAMP, 'out', 2000, 'tx-hash-2')",
                    {"uid": other_id},
                ),
                ("INSERT INTO evidence_items (id, user_pk, transaction_id, file_key) VALUES (1, :uid, 1, 'evidence/file.pdf')", {"uid": owner_id}),
                ("INSERT INTO evidence_items (id, user_pk, transaction_id, file_key) VALUES (2, :uid, 2, 'other/evidence.pdf')", {"uid": other_id}),
                ("INSERT INTO income_labels (id, user_pk, transaction_id) VALUES (1, :uid, 1)", {"uid": owner_id}),
                ("INSERT INTO expense_labels (id, user_pk, transaction_id) VALUES (1, :uid, 1)", {"uid": owner_id}),
                ("INSERT INTO receipt_batches (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO receipt_items (id, user_pk, batch_id, file_key) VALUES (1, :uid, 1, 'receipt/file.pdf')", {"uid": owner_id}),
                (
                    "INSERT INTO receipt_expense_followup_answers (id, user_pk, transaction_id, evidence_item_id, question_key, answered_by) "
                    "VALUES (1, :uid, 1, 1, 'purpose', :uid)",
                    {"uid": owner_id},
                ),
                (
                    "INSERT INTO receipt_expense_followup_answers (id, user_pk, transaction_id, evidence_item_id, question_key, answered_by) "
                    "VALUES (2, :other_uid, 2, 2, 'cross', :owner_uid)",
                    {"other_uid": other_id, "owner_uid": owner_id},
                ),
                (
                    "INSERT INTO receipt_expense_reinforcements (id, user_pk, transaction_id, evidence_item_id, supporting_file_key, updated_by) "
                    "VALUES (1, :uid, 1, 1, 'reinforcement/support.pdf', :uid)",
                    {"uid": owner_id},
                ),
                (
                    "INSERT INTO receipt_expense_reinforcements (id, user_pk, transaction_id, evidence_item_id, supporting_file_key, updated_by) "
                    "VALUES (2, :other_uid, 2, 2, 'other/support.pdf', :owner_uid)",
                    {"other_uid": other_id, "owner_uid": owner_id},
                ),
                ("INSERT INTO official_data_documents (id, user_pk, raw_file_key) VALUES (1, :uid, 'official/raw.pdf')", {"uid": owner_id}),
                ("INSERT INTO nhis_user_profiles (id, user_pk, member_type) VALUES (1, :uid, 'regional')", {"uid": owner_id}),
                ("INSERT INTO nhis_bill_history (id, user_pk, bill_year, bill_month) VALUES (1, :uid, 2026, 3)", {"uid": owner_id}),
                ("INSERT INTO asset_profiles (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO asset_items (id, user_pk, kind) VALUES (1, :uid, 'car')", {"uid": owner_id}),
                ("INSERT INTO billing_customers (id, user_pk, customer_key) VALUES (1, :uid, 'cust_1')", {"uid": owner_id}),
                ("INSERT INTO billing_methods (id, user_pk, billing_customer_id) VALUES (1, :uid, 1)", {"uid": owner_id}),
                ("INSERT INTO billing_method_registration_attempts (id, user_pk, billing_customer_id) VALUES (1, :uid, 1)", {"uid": owner_id}),
                ("INSERT INTO billing_subscriptions (id, user_pk, billing_customer_id, billing_method_id) VALUES (1, :uid, 1, 1)", {"uid": owner_id}),
                ("INSERT INTO billing_subscription_items (id, subscription_id, user_pk) VALUES (1, 1, :uid)", {"uid": owner_id}),
                ("INSERT INTO billing_checkout_intents (id, user_pk, billing_method_id, related_subscription_id, resume_token) VALUES (1, :uid, 1, 1, 'resume_1')", {"uid": owner_id}),
                ("INSERT INTO billing_payment_attempts (id, user_pk, subscription_id, checkout_intent_id, order_id) VALUES (1, :uid, 1, 1, 'order_1')", {"uid": owner_id}),
                ("INSERT INTO billing_payment_events (id, user_pk, event_hash) VALUES (1, :uid, 'event_1')", {"uid": owner_id}),
                ("INSERT INTO entitlement_change_logs (id, user_pk, source_id) VALUES (1, :uid, 'src_1')", {"uid": owner_id}),
                ("INSERT INTO action_logs (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO recurring_candidates (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO counterparty_rules (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO counterparty_expense_rules (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO weekly_tasks (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO dashboard_snapshots (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO dashboard_entries (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO hold_decisions (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO tax_buffer_ledger (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO user_dashboard_state (user_pk) VALUES (:uid)", {"uid": owner_id}),
                ("INSERT INTO recurring_rules (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO csv_format_mappings (id, user_pk) VALUES (1, :uid)", {"uid": owner_id}),
                ("INSERT INTO inquiries (id, user_pk, subject) VALUES (1, :uid, 'hello')", {"uid": owner_id}),
                ("INSERT INTO refresh_tokens (id, user_pk, token_hash, replaced_by_id) VALUES (1, :uid, 'rt1', NULL)", {"uid": owner_id}),
                ("INSERT INTO refresh_tokens (id, user_pk, token_hash, replaced_by_id) VALUES (2, :uid, 'rt2', 1)", {"uid": owner_id}),
            ]
            for sql, params in inserts:
                db.session.execute(text(sql), params)
            db.session.commit()

            deleted_keys: list[str] = []

            def _delete_side_effect(file_key: str) -> None:
                deleted_keys.append(str(file_key))
                if str(file_key) == "official/raw.pdf":
                    raise RuntimeError("cleanup failed")
                if str(file_key) == "reinforcement/support.pdf":
                    raise FileNotFoundError(file_key)

            with patch("services.auth.delete_physical_file", side_effect=_delete_side_effect):
                ok, msg, file_delete_errors = delete_user_account(
                    user_pk=owner_id,
                    current_password="Password123!",
                    confirm_text="삭제",
                )

            self.assertTrue(ok, msg)
            self.assertEqual(file_delete_errors, 1)
            self.assertCountEqual(
                deleted_keys,
                [
                    "evidence/file.pdf",
                    "receipt/file.pdf",
                    "reinforcement/support.pdf",
                    "official/raw.pdf",
                ],
            )

            self.assertIsNone(User.query.filter_by(id=owner_id).first())
            self.assertEqual(self._count_for_user("settings", owner_id), 0)
            self.assertEqual(self._count_for_user("tax_profiles", owner_id), 0)
            self.assertEqual(self._count_for_user("transactions", owner_id), 0)
            self.assertEqual(self._count_for_user("evidence_items", owner_id), 0)
            self.assertEqual(self._count_for_user("receipt_items", owner_id), 0)
            self.assertEqual(self._count_for_user("receipt_batches", owner_id), 0)
            self.assertEqual(self._count_for_user("receipt_expense_followup_answers", owner_id), 0)
            self.assertEqual(self._count_for_user("receipt_expense_reinforcements", owner_id), 0)
            self.assertEqual(self._count_for_user("official_data_documents", owner_id), 0)
            self.assertEqual(self._count_for_user("user_bank_accounts", owner_id), 0)
            self.assertEqual(self._count_for_user("bank_account_links", owner_id), 0)
            self.assertEqual(self._count_for_user("nhis_user_profiles", owner_id), 0)
            self.assertEqual(self._count_for_user("nhis_bill_history", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_customers", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_methods", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_method_registration_attempts", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_checkout_intents", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_subscriptions", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_subscription_items", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_payment_attempts", owner_id), 0)
            self.assertEqual(self._count_for_user("billing_payment_events", owner_id), 0)
            self.assertEqual(self._count_for_user("entitlement_change_logs", owner_id), 0)
            self.assertEqual(self._count_for_user("refresh_tokens", owner_id), 0)

            other_followup = db.session.execute(
                text("SELECT answered_by FROM receipt_expense_followup_answers WHERE id = 2")
            ).scalar()
            other_reinforcement = db.session.execute(
                text("SELECT updated_by FROM receipt_expense_reinforcements WHERE id = 2")
            ).scalar()
            self.assertIsNone(other_followup)
            self.assertIsNone(other_reinforcement)


if __name__ == "__main__":
    unittest.main()
