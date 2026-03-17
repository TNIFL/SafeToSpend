from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from core.time import utcnow
from services import admin_ops


class AdminOpsReferenceWatchTest(unittest.TestCase):
    def test_missing_state_file_returns_warn_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing_path = Path(td) / "missing_state.json"
            info = admin_ops._build_reference_watch_freshness(state_path=missing_path)
            self.assertEqual(str(info.get("status") or ""), "warn")
            self.assertEqual(str(info.get("warn_reason") or ""), "missing")

    def test_changed_count_returns_warn_changed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
            payload = {
                "updated_at": "2026-03-07T00:00:00Z",
                "summary": {
                    "checked_count": 4,
                    "changed_count": 1,
                    "failing_count": 0,
                    "max_failure_streak": 0,
                },
            }
            state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            info = admin_ops._build_reference_watch_freshness(state_path=state_path)
            self.assertEqual(str(info.get("status") or ""), "warn")
            self.assertEqual(str(info.get("warn_reason") or ""), "changed")
            self.assertEqual(int(info.get("changed_count") or 0), 1)

    def test_stale_state_returns_warn_stale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
            stale_dt = utcnow() - timedelta(hours=admin_ops.REFERENCE_WATCH_STALE_HOURS + 24)
            stale_iso = stale_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            payload = {
                "updated_at": stale_iso,
                "summary": {
                    "checked_count": 2,
                    "changed_count": 0,
                    "failing_count": 0,
                    "max_failure_streak": 0,
                },
            }
            state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            info = admin_ops._build_reference_watch_freshness(state_path=state_path)
            self.assertEqual(str(info.get("status") or ""), "warn")
            self.assertEqual(str(info.get("warn_reason") or ""), "stale")


if __name__ == "__main__":
    unittest.main()
