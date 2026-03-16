from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.nhis_runtime import build_nhis_result_meta
from services.official_refs.guard import check_nhis_ready
from services.official_refs.registry import get_registry_hash


class NhisGuardReadyTest(unittest.TestCase):
    def _write_points(self, root: Path) -> Path:
        path = root / "points.json"
        path.write_text(
            json.dumps(
                {
                    "version": "test",
                    "rows": [
                        {"upper_krw": 10_000_000, "points": 100.0},
                        {"upper_krw": 20_000_000, "points": 200.0},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_manifest(
        self,
        root: Path,
        *,
        valid: bool,
        reason: str,
        mismatch_count: int = 0,
        network_error_count: int = 0,
        targets: dict | None = None,
    ) -> Path:
        path = root / "manifest.json"
        payload = {
            "valid": bool(valid),
            "reason": str(reason),
            "registry_hash": get_registry_hash(),
            "mismatch_count": int(mismatch_count),
            "network_error_count": int(network_error_count),
            "targets": targets or {},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _snapshot_ok(self) -> SimpleNamespace:
        return SimpleNamespace(
            effective_year=2026,
            health_insurance_rate=0.0719,
            long_term_care_ratio_of_health=0.1314,
            regional_point_value=211.5,
            property_basic_deduction_krw=100_000_000,
            is_active=True,
            sources_json={},
        )

    def test_snapshot_ok_allows_ready_when_manifest_is_artifact_only_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(
                root,
                valid=False,
                reason="value_mismatch",
                mismatch_count=2,
                network_error_count=0,
                targets={
                    "a": {"ok": False, "failure_reason": "offline_snapshot_missing"},
                    "b": {"ok": False, "failure_reason": "offline_snapshot_missing"},
                },
            )
            points = self._write_points(root)
            with patch("services.official_refs.guard.get_active_snapshot", return_value=self._snapshot_ok()):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertTrue(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "ok")
            self.assertEqual(str(status.get("guard_mode") or ""), "degraded_artifact_only")
            self.assertEqual(str(status.get("guard_warning") or ""), "official_snapshot_artifact_missing")
            self.assertIn("official_snapshot_artifact_missing", list(status.get("guard_warnings") or []))

    def test_format_drift_is_allowed_in_degraded_artifact_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(
                root,
                valid=False,
                reason="value_mismatch",
                mismatch_count=1,
                targets={"a": {"ok": False, "failure_reason": "offline_snapshot_missing"}},
            )
            points = self._write_points(root)
            snapshot = self._snapshot_ok()
            snapshot.sources_json = {"format_drift_detected": True}
            with patch("services.official_refs.guard.get_active_snapshot", return_value=snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertTrue(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "ok")
            self.assertIn("snapshot_format_drift_detected", list(status.get("guard_warnings") or []))

    def test_format_drift_blocks_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(root, valid=True, reason="ok")
            points = self._write_points(root)
            snapshot = self._snapshot_ok()
            snapshot.sources_json = {"format_drift_detected": True}
            with patch("services.official_refs.guard.get_active_snapshot", return_value=snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "snapshot_format_drift_or_fallback")

    def test_snapshot_value_mismatch_keeps_blocked_even_in_degraded_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(
                root,
                valid=False,
                reason="value_mismatch",
                mismatch_count=1,
                targets={
                    "a": {"ok": False, "failure_reason": "offline_snapshot_missing"},
                },
            )
            points = self._write_points(root)
            mismatched_snapshot = SimpleNamespace(
                effective_year=2026,
                health_insurance_rate=0.0700,
                long_term_care_ratio_of_health=0.1314,
                regional_point_value=211.5,
                property_basic_deduction_krw=100_000_000,
                is_active=True,
                sources_json={},
            )
            with patch("services.official_refs.guard.get_active_snapshot", return_value=mismatched_snapshot):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "snapshot_value_mismatch")
            self.assertIn("health_insurance_rate", list(status.get("snapshot_value_mismatches") or []))

    def test_manifest_content_validation_failure_stays_value_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(
                root,
                valid=False,
                reason="value_mismatch",
                mismatch_count=1,
                targets={
                    "a": {"ok": False, "failure_reason": "content_validation_failed"},
                },
            )
            points = self._write_points(root)
            with patch("services.official_refs.guard.get_active_snapshot", return_value=self._snapshot_ok()):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "value_mismatch")

    def test_missing_snapshot_reason_kept(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = self._write_manifest(root, valid=True, reason="ok")
            points = self._write_points(root)
            with patch("services.official_refs.guard.get_active_snapshot", return_value=None):
                status = check_nhis_ready(manifest_path=manifest, property_points_path=points)
            self.assertFalse(bool(status.get("ready")))
            self.assertEqual(str(status.get("reason") or ""), "snapshot_missing")

    def test_result_meta_distinguishes_stale_vs_missing_snapshot(self) -> None:
        stale_meta = build_nhis_result_meta(
            estimate={"member_type": "regional", "confidence_level": "high", "can_estimate": True},
            status={"is_stale": True, "update_error": "", "is_fallback_default": False},
            official_ready=True,
            profile={
                "member_type": "regional",
                "annual_income_krw": 30_000_000,
                "non_salary_annual_income_krw": 0,
                "property_tax_base_total_krw": 100_000_000,
            },
        )
        self.assertEqual(str(stale_meta.get("level") or ""), "limited")
        self.assertEqual(str(stale_meta.get("reason") or ""), "dataset_fallback")

        missing_meta = build_nhis_result_meta(
            estimate={"confidence_level": "high", "can_estimate": True},
            status={"is_stale": False, "update_error": "", "is_fallback_default": False},
            official_ready=False,
            profile={},
        )
        self.assertEqual(str(missing_meta.get("level") or ""), "blocked")
        self.assertEqual(str(missing_meta.get("reason") or ""), "missing_snapshot")


if __name__ == "__main__":
    unittest.main()
