from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from services.billing.startup_checks import (
    BillingStartupCheckError,
    resolve_billing_guard_mode,
    run_billing_startup_checks,
)


class BillingStartupChecksTest(unittest.TestCase):
    def test_resolve_mode(self) -> None:
        self.assertEqual(resolve_billing_guard_mode("production"), "strict")
        self.assertEqual(resolve_billing_guard_mode("staging"), "strict")
        self.assertEqual(resolve_billing_guard_mode("development"), "warn")
        self.assertEqual(resolve_billing_guard_mode("development", override="strict"), "strict")
        self.assertEqual(resolve_billing_guard_mode("production", override="off"), "off")

    def test_warn_mode_does_not_raise(self) -> None:
        app = Flask(__name__)
        app.config["APP_ENV"] = "development"
        app.config["BILLING_GUARD_MODE"] = "warn"
        with (
            patch("services.billing.startup_checks._validate_required_env", return_value=["missing env"]),
            patch("services.billing.startup_checks._validate_required_schema", return_value=["missing schema"]),
        ):
            report = run_billing_startup_checks(app)
        self.assertFalse(report.ok)
        self.assertEqual(report.mode, "warn")
        self.assertEqual(len(report.errors), 2)

    def test_strict_mode_raises(self) -> None:
        app = Flask(__name__)
        app.config["APP_ENV"] = "production"
        app.config["BILLING_GUARD_MODE"] = "strict"
        with (
            patch("services.billing.startup_checks._validate_required_env", return_value=["missing env"]),
            patch("services.billing.startup_checks._validate_required_schema", return_value=[]),
        ):
            with self.assertRaises(BillingStartupCheckError):
                run_billing_startup_checks(app)


if __name__ == "__main__":
    unittest.main()
