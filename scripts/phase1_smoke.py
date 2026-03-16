#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from core.extensions import db
from domain.models import User
from scripts.reference_watchdog import run_watchdog
from services.assets_data import ensure_asset_datasets
from services.nhis_rates import ensure_active_snapshot
from services.official_refs.guard import check_nhis_ready
from services.official_refs.registry import get_registry_hash


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "") -> None:
        self.status_code = int(status_code)
        self.text = str(text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")


def _assert_nhis_runtime_no_refresh() -> None:
    with patch("services.nhis_rates.get_active_snapshot", return_value=None):
        with patch("services.nhis_rates.refresh_nhis_rates", side_effect=AssertionError("refresh should not run")):
            status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6, force_refresh=True)

    assert status.snapshot is None, "snapshot_missing should return None snapshot"
    assert str(status.update_error or "") == "snapshot_missing", "unexpected snapshot missing reason"


def _assert_assets_runtime_no_refresh() -> None:
    with patch("services.assets_data.get_active_dataset_snapshot", return_value=None):
        with patch("services.assets_data.fetch_asset_datasets", side_effect=AssertionError("fetch should not run")):
            with patch("services.assets_data._upsert_snapshot", side_effect=AssertionError("upsert should not run")):
                status = ensure_asset_datasets(refresh_if_stale_days=30, force_refresh=True)

    assert bool(status.used_fallback), "dataset_missing should mark fallback"
    assert str(status.update_error or "") == "runtime_refresh_blocked", "runtime refresh block not applied"


def _assert_property_table_gate() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "valid": True,
                    "registry_hash": get_registry_hash(),
                    "reason": "ok",
                    "mismatch_count": 0,
                    "network_error_count": 0,
                }
            ),
            encoding="utf-8",
        )

        fake_snapshot = SimpleNamespace(
            effective_year=2026,
            is_active=True,
            health_insurance_rate=0.0719,
            long_term_care_ratio_of_health=0.1314,
            regional_point_value=211.5,
            property_basic_deduction_krw=100_000_000,
            sources_json={},
        )
        with patch("services.official_refs.guard.get_active_snapshot", return_value=fake_snapshot):
            status = check_nhis_ready(
                manifest_path=manifest,
                property_points_path=(root / "nhis_property_points_2026.json"),
            )

        assert not bool(status.get("ready")), "gate should fail when property table is missing"
        assert str(status.get("reason") or "") == "property_points_table_missing", "unexpected gate fail reason"


def _assert_reference_watchdog_schema() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config_path = root / "targets.json"
        state_path = root / "status.json"
        config_path.write_text(
            json.dumps(
                {
                    "allowed_domains": ["example.com"],
                    "targets": [
                        {
                            "key": "sample_ref",
                            "url": "https://example.com/rules",
                            "patterns": ["공식"],
                            "keywords": ["공식", "기준"],
                            "timeout": 2,
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        with patch("scripts.reference_watchdog.requests.Session.get") as mocked_get:
            mocked_get.return_value = _FakeResponse(text="공식 기준 문서 테스트")
            payload, code = run_watchdog(config_path=config_path, state_path=state_path, timeout=2, strict=False)

        assert code == 0, "watchdog strict off should pass"
        assert bool(payload.get("summary")), "watchdog summary missing"
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        for required_key in ("last_checked_at", "changed", "failing", "fail_streak", "notes"):
            assert required_key in saved, f"watchdog status missing key: {required_key}"


def _assert_nhis_request_path_no_network() -> None:
    app = create_app()
    with app.app_context():
        user = User.query.order_by(User.id.asc()).first()
        if user is None:
            user = User(email="phase1-smoke@safetospend.local")
            user.set_password("Phase1Smoke123!")
            db.session.add(user)
            db.session.commit()
        user_id = int(user.id)

    def _blocked_network(*_args, **_kwargs):
        raise AssertionError("network_call_detected_during_request")

    with patch("requests.sessions.Session.request", side_effect=_blocked_network):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = user_id
            response = client.get("/dashboard/nhis?month=2026-03", follow_redirects=False)
            assert int(response.status_code) in {200, 302}, f"unexpected status={response.status_code}"


def main() -> int:
    _assert_nhis_runtime_no_refresh()
    _assert_assets_runtime_no_refresh()
    _assert_property_table_gate()
    _assert_reference_watchdog_schema()
    _assert_nhis_request_path_no_network()
    print("PASS: phase1 hard gate smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
