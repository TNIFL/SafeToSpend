from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.billing.service import cleanup_registration_attempts, normalize_registration_attempts_abandoned


class _Comparable:
    def __le__(self, _other):
        return True


class BillingRegistrationCleanupTest(unittest.TestCase):
    def test_normalize_abandoned_no_rows(self) -> None:
        chain = MagicMock()
        chain.all.return_value = []
        with (
            patch("services.billing.service.BillingMethodRegistrationAttempt") as attempt_cls,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            attempt_cls.started_at = _Comparable()
            attempt_cls.query.filter_by.return_value.filter.return_value = chain
            changed = normalize_registration_attempts_abandoned(
                abandoned_after_hours=2,
                now=datetime(2026, 3, 9, tzinfo=timezone.utc),
            )
        self.assertEqual(changed, 0)
        commit_mock.assert_not_called()

    def test_normalize_abandoned_updates_rows(self) -> None:
        row_a = SimpleNamespace(status="registration_started", fail_code=None, fail_message_norm=None, completed_at=None, updated_at=None)
        row_b = SimpleNamespace(status="registration_started", fail_code="", fail_message_norm="", completed_at=None, updated_at=None)
        chain = MagicMock()
        chain.all.return_value = [row_a, row_b]
        with (
            patch("services.billing.service.BillingMethodRegistrationAttempt") as attempt_cls,
            patch("services.billing.service.db.session.add") as add_mock,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            attempt_cls.started_at = _Comparable()
            attempt_cls.query.filter_by.return_value.filter.return_value = chain
            changed = normalize_registration_attempts_abandoned(
                abandoned_after_hours=2,
                now=datetime(2026, 3, 9, tzinfo=timezone.utc),
            )
        self.assertEqual(changed, 2)
        self.assertEqual(row_a.status, "canceled")
        self.assertEqual(row_a.fail_code, "abandoned")
        self.assertEqual(row_b.status, "canceled")
        self.assertEqual(add_mock.call_count, 2)
        commit_mock.assert_called_once()

    def test_cleanup_registration_attempts_dry_run(self) -> None:
        row_a = SimpleNamespace(id=1)
        row_b = SimpleNamespace(id=2)
        chain = MagicMock()
        chain.all.return_value = [row_a, row_b]
        with (
            patch("services.billing.service.BillingMethodRegistrationAttempt") as attempt_cls,
            patch("services.billing.service.db.session.delete") as delete_mock,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            attempt_cls.updated_at = _Comparable()
            attempt_cls.query.filter.return_value.filter.return_value = chain
            result = cleanup_registration_attempts(
                retention_days=90,
                dry_run=True,
                now=datetime(2026, 3, 9, tzinfo=timezone.utc),
            )
        self.assertEqual(result["purged_count"], 2)
        delete_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_cleanup_registration_attempts_real_delete(self) -> None:
        row_a = SimpleNamespace(id=1)
        row_b = SimpleNamespace(id=2)
        chain = MagicMock()
        chain.all.return_value = [row_a, row_b]
        with (
            patch("services.billing.service.BillingMethodRegistrationAttempt") as attempt_cls,
            patch("services.billing.service.db.session.delete") as delete_mock,
            patch("services.billing.service.db.session.commit") as commit_mock,
        ):
            attempt_cls.updated_at = _Comparable()
            attempt_cls.query.filter.return_value.filter.return_value = chain
            result = cleanup_registration_attempts(
                retention_days=90,
                dry_run=False,
                now=datetime(2026, 3, 9, tzinfo=timezone.utc),
            )
        self.assertEqual(result["purged_count"], 2)
        self.assertEqual(delete_mock.call_count, 2)
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
