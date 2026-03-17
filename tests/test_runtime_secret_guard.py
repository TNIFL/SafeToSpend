from __future__ import annotations

import unittest

from flask import Flask

from core.runtime_secret_guard import DEFAULT_SECRET_KEY, validate_runtime_secret_key
from services.api_tokens import build_access_token


class RuntimeSecretGuardTest(unittest.TestCase):
    def test_default_secret_rejected_for_external_runtime(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_runtime_secret_key(
                secret=DEFAULT_SECRET_KEY,
                app_env="production",
                bind_host="0.0.0.0",
                environ={},
                argv=["gunicorn", "app:app"],
            )

    def test_default_secret_allowed_for_local_dev(self) -> None:
        validate_runtime_secret_key(
            secret=DEFAULT_SECRET_KEY,
            app_env="development",
            bind_host="127.0.0.1",
            environ={},
            argv=["flask", "--app", "app", "run"],
        )

    def test_missing_app_env_does_not_allow_default_secret(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_runtime_secret_key(
                secret=DEFAULT_SECRET_KEY,
                app_env="",
                bind_host="",
                environ={},
                argv=["gunicorn", "app:app"],
            )

    def test_non_default_secret_allowed(self) -> None:
        validate_runtime_secret_key(
            secret="runtime-secret-for-tests",
            app_env="production",
            bind_host="0.0.0.0",
            environ={},
            argv=["gunicorn", "app:app"],
        )

    def test_access_token_signer_uses_same_guard(self) -> None:
        app = Flask(__name__)
        app.config["SECRET_KEY"] = DEFAULT_SECRET_KEY
        app.config["APP_ENV"] = "production"
        app.config["RUNTIME_BIND_HOST"] = "0.0.0.0"

        with app.app_context():
            with self.assertRaises(RuntimeError):
                build_access_token(user_pk=1)


if __name__ == "__main__":
    unittest.main()
