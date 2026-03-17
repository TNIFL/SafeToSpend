from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from services.assets_data import ensure_asset_datasets
from services.nhis_rates import ensure_active_snapshot


class ReferenceGateRuntimeTest(unittest.TestCase):
    def test_ensure_active_snapshot_never_refreshes_in_runtime(self) -> None:
        with patch("services.nhis_rates.get_active_snapshot", return_value=None):
            with patch("services.nhis_rates.refresh_nhis_rates", side_effect=AssertionError("refresh should not run")):
                status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6, force_refresh=True)
        self.assertIsNone(status.snapshot)
        self.assertEqual(str(status.update_error or ""), "snapshot_missing")
        self.assertTrue(bool(status.is_stale))

    def test_ensure_active_snapshot_marks_stale_without_network(self) -> None:
        stale_row = SimpleNamespace(
            fetched_at=(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=40)),
            sources_json={},
        )
        with patch("services.nhis_rates.get_active_snapshot", return_value=stale_row):
            with patch("services.nhis_rates.refresh_nhis_rates", side_effect=AssertionError("refresh should not run")):
                status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6, force_refresh=False)
        self.assertIsNotNone(status.snapshot)
        self.assertTrue(bool(status.is_stale))
        self.assertEqual(status.update_error, None)

    def test_ensure_asset_datasets_runtime_is_read_only(self) -> None:
        with patch("services.assets_data.get_active_dataset_snapshot", return_value=None):
            with patch("services.assets_data.fetch_asset_datasets", side_effect=AssertionError("fetch should not run")):
                with patch("services.assets_data._upsert_snapshot", side_effect=AssertionError("upsert should not run")):
                    status = ensure_asset_datasets(refresh_if_stale_days=30, force_refresh=True)
        self.assertTrue(bool(status.used_fallback))
        self.assertTrue(bool(status.is_stale))
        self.assertEqual(str(status.update_error or ""), "runtime_refresh_blocked")


if __name__ == "__main__":
    unittest.main()
