from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.official_refs.guard import check_nhis_ready, get_official_guard_status
from services.official_refs.registry import get_registry_hash


class OfficialRefsGuardTest(unittest.TestCase):
    def test_missing_manifest_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = get_official_guard_status(Path(td) / "manifest.json")
            self.assertFalse(bool(status.get("valid")))
            self.assertEqual(str(status.get("reason")), "not_verified")

    def test_hash_mismatch_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            payload = {
                "valid": True,
                "registry_hash": "different",
                "reason": "ok",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            status = get_official_guard_status(path)
            self.assertFalse(bool(status.get("valid")))
            self.assertEqual(str(status.get("reason")), "registry_hash_mismatch")

    def test_valid_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            payload = {
                "valid": True,
                "registry_hash": get_registry_hash(),
                "reason": "ok",
                "mismatch_count": 0,
                "network_error_count": 0,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            status = get_official_guard_status(path)
            self.assertTrue(bool(status.get("valid")))
            self.assertEqual(str(status.get("reason")), "ok")

    def test_check_nhis_ready_requires_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "registry_hash": get_registry_hash(),
                        "reason": "ok",
                    }
                ),
                encoding="utf-8",
            )
            with patch("services.official_refs.guard.get_active_snapshot", return_value=None):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=(root / "points.json"))
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "snapshot_missing")

    def test_check_nhis_ready_requires_property_table(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "registry_hash": get_registry_hash(),
                        "reason": "ok",
                    }
                ),
                encoding="utf-8",
            )
            fake_snapshot = SimpleNamespace(
                effective_year=2026,
                health_insurance_rate=0.0719,
                long_term_care_ratio_of_health=0.1314,
                regional_point_value=211.5,
                property_basic_deduction_krw=100_000_000,
                sources_json={},
            )
            with patch("services.official_refs.guard.get_active_snapshot", return_value=fake_snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=(root / "missing_points.json"))
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "property_points_table_missing")

    def test_check_nhis_ready_passes_with_snapshot_and_table(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            points = root / "points.json"
            manifest.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "registry_hash": get_registry_hash(),
                        "reason": "ok",
                    }
                ),
                encoding="utf-8",
            )
            points.write_text(
                json.dumps(
                    {
                        "version": "test",
                        "rows": [
                            {"upper_krw": 10_000_000, "points": 150.0},
                            {"upper_krw": 20_000_000, "points": 250.0},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            fake_snapshot = SimpleNamespace(
                effective_year=2026,
                health_insurance_rate=0.0719,
                long_term_care_ratio_of_health=0.1314,
                regional_point_value=211.5,
                property_basic_deduction_krw=100_000_000,
                sources_json={},
            )
            with patch("services.official_refs.guard.get_active_snapshot", return_value=fake_snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertTrue(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "ok")

    def test_check_nhis_ready_rejects_inactive_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            points = root / "points.json"
            manifest.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "registry_hash": get_registry_hash(),
                        "reason": "ok",
                    }
                ),
                encoding="utf-8",
            )
            points.write_text(
                json.dumps({"rows": [{"upper_krw": 10_000_000, "points": 100.0}]}),
                encoding="utf-8",
            )
            fake_snapshot = SimpleNamespace(
                effective_year=2026,
                health_insurance_rate=0.0719,
                long_term_care_ratio_of_health=0.1314,
                regional_point_value=211.5,
                property_basic_deduction_krw=100_000_000,
                is_active=False,
                sources_json={},
            )
            with patch("services.official_refs.guard.get_active_snapshot", return_value=fake_snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "snapshot_inactive")


if __name__ == "__main__":
    unittest.main()
