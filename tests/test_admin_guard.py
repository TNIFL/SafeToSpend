from __future__ import annotations

import unittest

from flask import Flask
from sqlalchemy import text

from core.admin_guard import admin_required
from core.extensions import db
from domain.models import User
from services.auth import register_user, set_user_admin_role


class AdminGuardRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "admin-guard-test"
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)

        @app.get("/login", endpoint="web_auth.login")
        def _login():  # pragma: no cover
            return "login"

        @app.get("/admin-only")
        @admin_required
        def _admin_only():  # pragma: no cover
            return "ok"

        self.app = app
        self.client = app.test_client()

        with self.app.app_context():
            db.session.execute(
                text(
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
                    """
                )
            )
            db.session.execute(
                text(
                    """
                    CREATE TABLE settings (
                        user_pk INTEGER PRIMARY KEY,
                        default_tax_rate FLOAT NOT NULL,
                        custom_rates TEXT NOT NULL,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_pk) REFERENCES users(id)
                    )
                    """
                )
            )
            db.session.commit()

    def _login_user(self, user_id: int) -> None:
        with self.client.session_transaction() as sess:
            sess["user_id"] = int(user_id)

    def test_regular_registered_user_cannot_access_admin_route(self) -> None:
        with self.app.app_context():
            ok, msg = register_user("member@example.com", "Password123!")
            self.assertTrue(ok, msg)
            user = User.query.filter_by(email="member@example.com").first()
            self.assertIsNotNone(user)
            self.assertFalse(bool(user.is_admin))
            self._login_user(int(user.id))

        resp = self.client.get("/admin-only")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("관리자만 접근할 수 있어요.", resp.get_data(as_text=True))

    def test_candidate_admin_email_is_not_admin_without_explicit_flag(self) -> None:
        with self.app.app_context():
            ok, msg = register_user("tnifl@naver.com", "Password123!")
            self.assertTrue(ok, msg)
            user = User.query.filter_by(email="tnifl@naver.com").first()
            self.assertIsNotNone(user)
            self.assertFalse(bool(user.is_admin))
            self._login_user(int(user.id))

        resp = self.client.get("/admin-only")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("관리자만 접근할 수 있어요.", resp.get_data(as_text=True))

    def test_explicit_admin_flag_allows_admin_route(self) -> None:
        with self.app.app_context():
            ok, msg = register_user("real-admin@example.com", "Password123!")
            self.assertTrue(ok, msg)
            ok, msg, user_id = set_user_admin_role(email="real-admin@example.com", is_admin=True)
            self.assertTrue(ok, msg)
            self.assertIsNotNone(user_id)
            user = User.query.filter_by(email="real-admin@example.com").first()
            self.assertTrue(bool(user and user.is_admin))
            self._login_user(int(user.id))

        resp = self.client.get("/admin-only")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(as_text=True), "ok")


if __name__ == "__main__":
    unittest.main()
