from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

from app import create_app
from services.bank_sync_scheduler import BankSyncBatchResult


def _batch_result(*, dry_run: bool, failed_count: int) -> BankSyncBatchResult:
    now = datetime(2026, 3, 14, 12, 0, 0)
    return BankSyncBatchResult(
        mode="auto_due",
        dry_run=bool(dry_run),
        started_at=now,
        finished_at=now,
        total_links=3,
        due_links=2,
        processed_links=2,
        success_count=(2 - int(failed_count)),
        failed_count=int(failed_count),
        skipped_interval_count=1,
        skipped_plan_count=0,
        skipped_lock_count=0,
        skipped_limit_count=0,
        inserted_rows_total=4,
        duplicate_rows_total=2,
        failed_rows_total=(1 if int(failed_count) > 0 else 0),
        results=[],
        errors=([{"account": "0004-****1234", "error": "partial_failed:1"}] if int(failed_count) > 0 else []),
    )


class BankAutoSyncCliTest(unittest.TestCase):
    def _make_app(self):
        with patch.dict(
            os.environ,
            {
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SECRET_KEY": "test-secret",
                "APP_ENV": "test",
            },
            clear=False,
        ):
            with patch.object(sys, "argv", ["pytest", "db"]):
                return create_app()

    def test_cli_help_exposes_expected_options(self) -> None:
        app = self._make_app()
        runner = app.test_cli_runner()
        result = runner.invoke(args=["bank-sync-run-due", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--dry-run", result.output)
        self.assertIn("--limit", result.output)
        self.assertIn("--account-id", result.output)

    def test_cli_dry_run_calls_due_service(self) -> None:
        app = self._make_app()
        runner = app.test_cli_runner()
        with patch(
            "services.bank_sync_scheduler.run_due_bank_sync_batch",
            return_value=_batch_result(dry_run=True, failed_count=0),
        ) as mocked:
            result = runner.invoke(
                args=["bank-sync-run-due", "--dry-run", "--limit", "7", "--account-id", "11", "--user-pk", "5"]
            )
        self.assertEqual(result.exit_code, 0)
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["dry_run"])
        self.assertEqual(kwargs["limit"], 7)
        self.assertEqual(kwargs["account_id"], 11)
        self.assertEqual(kwargs["user_pk"], 5)
        self.assertIn('"dry_run": true', result.output.lower())

    def test_cli_returns_exit_2_when_failures_exist(self) -> None:
        app = self._make_app()
        runner = app.test_cli_runner()
        with patch(
            "services.bank_sync_scheduler.run_due_bank_sync_batch",
            return_value=_batch_result(dry_run=False, failed_count=1),
        ):
            result = runner.invoke(args=["bank-sync-run-due"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn('"failed_count": 1', result.output)


if __name__ == "__main__":
    unittest.main()

