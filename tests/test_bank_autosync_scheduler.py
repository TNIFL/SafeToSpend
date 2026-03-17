from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.bank_sync_scheduler import (
    BankSyncLinkDecision,
    evaluate_due_links,
    run_bank_sync_batch,
    run_due_bank_sync_batch,
    run_manual_bank_sync_batch,
)
from services.import_popbill import PopbillImportError, PopbillImportResult


ROOT = Path(__file__).resolve().parents[1]


def _fake_link(
    *,
    link_id: int,
    user_pk: int,
    last_synced_at: datetime | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=int(link_id),
        user_pk=int(user_pk),
        last_synced_at=last_synced_at,
        bank_code="0004",
        account_number=f"1234{int(link_id):04d}",
    )


class BankAutoSyncSchedulerTest(unittest.TestCase):
    def test_evaluate_due_links_uses_interval_source_of_truth(self) -> None:
        now = datetime(2026, 3, 14, 10, 0, 0)
        links = [
            _fake_link(link_id=1, user_pk=10, last_synced_at=None),
            _fake_link(link_id=2, user_pk=11, last_synced_at=now - timedelta(minutes=30)),
            _fake_link(link_id=3, user_pk=12, last_synced_at=now - timedelta(minutes=300)),
        ]
        interval_map = {10: 60, 11: 60, 12: None}
        with patch(
            "services.bank_sync_scheduler._resolve_interval_minutes",
            side_effect=lambda user_pk: interval_map.get(int(user_pk)),
        ):
            decisions, summary = evaluate_due_links(links, now=now, force_run=False)
        decision_by_id = {int(d.link_id): d for d in decisions}
        self.assertTrue(bool(decision_by_id[1].due))
        self.assertEqual(decision_by_id[2].skip_reason, "interval_not_elapsed")
        self.assertEqual(decision_by_id[3].skip_reason, "plan_interval_unavailable")
        self.assertEqual(int(summary["due"]), 1)
        self.assertEqual(int(summary["skipped_interval"]), 1)
        self.assertEqual(int(summary["skipped_plan"]), 1)

    def test_run_batch_skips_locked_link(self) -> None:
        link = _fake_link(link_id=101, user_pk=21, last_synced_at=None)
        decisions = [
            BankSyncLinkDecision(
                link_id=101,
                user_pk=21,
                interval_minutes=60,
                last_synced_at=None,
                due=True,
                skip_reason=None,
            )
        ]
        with (
            patch("services.bank_sync_scheduler.list_active_links", return_value=[link]),
            patch("services.bank_sync_scheduler.evaluate_due_links", return_value=(decisions, {"due": 1, "skipped_interval": 0, "skipped_plan": 0})),
            patch("services.bank_sync_scheduler.try_acquire_bank_link_lock", return_value=False),
            patch("services.bank_sync_scheduler.release_bank_link_lock"),
            patch("services.bank_sync_scheduler._run_single_link_sync"),
        ):
            result = run_bank_sync_batch(mode="auto_due", dry_run=False)
        self.assertEqual(result.processed_links, 0)
        self.assertEqual(result.skipped_lock_count, 1)
        row = next(r for r in result.results if int(r.link_id) == 101)
        self.assertEqual(row.status, "skipped_locked")

    def test_run_batch_isolates_failures_per_link(self) -> None:
        link1 = _fake_link(link_id=201, user_pk=31, last_synced_at=None)
        link2 = _fake_link(link_id=202, user_pk=31, last_synced_at=None)
        decisions = [
            BankSyncLinkDecision(201, 31, 60, None, True, None),
            BankSyncLinkDecision(202, 31, 60, None, True, None),
        ]
        ok_piece = PopbillImportResult(
            import_job_id=1,
            total_rows=12,
            inserted_rows=4,
            duplicate_rows=8,
            failed_rows=0,
            errors=[],
            requested_ranges=1,
            succeeded_ranges=1,
            failed_ranges=0,
        )

        def _sync_side_effect(*, link, use_backfill_3m):
            if int(link.id) == 201:
                raise PopbillImportError("계좌 201 실패")
            return ok_piece

        with (
            patch("services.bank_sync_scheduler.list_active_links", return_value=[link1, link2]),
            patch("services.bank_sync_scheduler.evaluate_due_links", return_value=(decisions, {"due": 2, "skipped_interval": 0, "skipped_plan": 0})),
            patch("services.bank_sync_scheduler.try_acquire_bank_link_lock", return_value=True),
            patch("services.bank_sync_scheduler.release_bank_link_lock"),
            patch("services.bank_sync_scheduler._run_single_link_sync", side_effect=_sync_side_effect),
            patch("services.bank_sync_scheduler.db.session.rollback"),
        ):
            result = run_bank_sync_batch(mode="auto_due", dry_run=False)

        self.assertEqual(result.processed_links, 2)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failed_count, 1)
        status_map = {int(r.link_id): r.status for r in result.results if int(r.link_id) in {201, 202}}
        self.assertEqual(status_map[201], "error")
        self.assertEqual(status_map[202], "success")

    def test_due_and_manual_wrappers_call_common_batch(self) -> None:
        with patch("services.bank_sync_scheduler.run_bank_sync_batch") as mocked:
            mocked.return_value = SimpleNamespace(mode="auto_due")
            run_due_bank_sync_batch(dry_run=True, limit=5, account_id=10, user_pk=77)
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["mode"], "auto_due")
            self.assertTrue(kwargs["dry_run"])
            self.assertEqual(kwargs["limit"], 5)
            self.assertFalse(kwargs["force_run"])

        with patch("services.bank_sync_scheduler.run_bank_sync_batch") as mocked:
            mocked.return_value = SimpleNamespace(mode="manual")
            run_manual_bank_sync_batch(user_pk=77, use_backfill_3m=True, link_id=11, dry_run=False)
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["mode"], "manual_backfill_3m")
            self.assertTrue(kwargs["force_run"])
            self.assertTrue(kwargs["use_backfill_3m"])
            self.assertEqual(kwargs["account_id"], 11)

    def test_manual_route_uses_common_scheduler_service(self) -> None:
        source = (ROOT / "routes" / "web" / "bank.py").read_text(encoding="utf-8")
        self.assertIn("run_manual_bank_sync_batch(", source)
        self.assertNotIn("sync_popbill_for_user(", source)
        self.assertNotIn("sync_popbill_backfill_max_3m(", source)


if __name__ == "__main__":
    unittest.main()

