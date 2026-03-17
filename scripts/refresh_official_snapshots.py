#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.assets_data import get_active_dataset_snapshot, refresh_asset_datasets  # noqa: E402
from services.nhis_rates import get_active_snapshot, snapshot_to_display_dict  # noqa: E402
from services.official_refs.registry import OFFICIAL_REFERENCE_YEAR  # noqa: E402
from scripts.verify_official_refs import run_verify  # noqa: E402

RUN_LOG_PATH = ROOT / "data" / "official_snapshots" / "run_log.json"
MANIFEST_PATH = ROOT / "data" / "official_snapshots" / "manifest.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        if not path.exists():
            return dict(fallback or {})
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return dict(fallback or {})
    return dict(fallback or {})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_nhis_snapshot_meta() -> dict[str, Any]:
    try:
        snapshot = get_active_snapshot()
    except Exception as exc:
        return {
            "id": None,
            "effective_year": None,
            "fetched_at": None,
            "content_hash": "",
            "error": type(exc).__name__,
        }
    if snapshot is None:
        return {"id": None, "effective_year": None, "fetched_at": None, "content_hash": ""}
    payload = snapshot_to_display_dict(snapshot)
    return {
        "id": int(getattr(snapshot, "id", 0) or 0),
        "effective_year": int(payload.get("effective_year") or 0),
        "fetched_at": str(getattr(snapshot, "fetched_at", "") or ""),
        "content_hash": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def _build_asset_snapshot_meta(dataset_key: str) -> dict[str, Any]:
    try:
        row = get_active_dataset_snapshot(dataset_key)
    except Exception as exc:
        return {
            "id": None,
            "dataset_key": dataset_key,
            "version_year": None,
            "fetched_at": None,
            "content_hash": "",
            "error": type(exc).__name__,
        }
    if row is None:
        return {"id": None, "dataset_key": dataset_key, "version_year": None, "fetched_at": None, "content_hash": ""}
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return {
        "id": int(getattr(row, "id", 0) or 0),
        "dataset_key": str(dataset_key),
        "version_year": int(getattr(row, "version_year", 0) or 0),
        "fetched_at": str(getattr(row, "fetched_at", "") or ""),
        "content_hash": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def run_refresh(*, timeout: int = 10, verify_timeout: int = 12, verify_offline: bool = False) -> int:
    started_at = _utc_now_iso()
    started_tick = time.perf_counter()
    steps: list[dict[str, Any]] = []
    errors: list[str] = []
    result_ok = True

    from app import create_app

    app = create_app()
    with app.app_context():
        # Step 1: NHIS rates refresh
        step_start = time.perf_counter()
        try:
            from services.nhis_rates import refresh_nhis_rates

            snap = refresh_nhis_rates(timeout=max(3, int(timeout)))
            steps.append(
                {
                    "name": "refresh_nhis_rates",
                    "ok": True,
                    "snapshot_id": int(getattr(snap, "id", 0) or 0),
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )
        except Exception as exc:
            result_ok = False
            errors.append(f"refresh_nhis_rates:{type(exc).__name__}")
            steps.append(
                {
                    "name": "refresh_nhis_rates",
                    "ok": False,
                    "error": type(exc).__name__,
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )

        # Step 2: asset dataset refresh
        step_start = time.perf_counter()
        try:
            refresh_asset_datasets(timeout=max(3, int(timeout)))
            steps.append(
                {
                    "name": "refresh_asset_datasets",
                    "ok": True,
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )
        except Exception as exc:
            result_ok = False
            errors.append(f"refresh_asset_datasets:{type(exc).__name__}")
            steps.append(
                {
                    "name": "refresh_asset_datasets",
                    "ok": False,
                    "error": type(exc).__name__,
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )

        # Step 3: official verify
        step_start = time.perf_counter()
        try:
            verify_code = run_verify(
                target_year=int(OFFICIAL_REFERENCE_YEAR),
                timeout_sec=max(4, int(verify_timeout)),
                offline=bool(verify_offline),
            )
            verify_ok = int(verify_code) == 0
            if not verify_ok:
                result_ok = False
                errors.append("verify_official_refs:FAILED")
            steps.append(
                {
                    "name": "verify_official_refs",
                    "ok": bool(verify_ok),
                    "exit_code": int(verify_code),
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )
        except Exception as exc:
            result_ok = False
            errors.append(f"verify_official_refs:{type(exc).__name__}")
            steps.append(
                {
                    "name": "verify_official_refs",
                    "ok": False,
                    "error": type(exc).__name__,
                    "duration_ms": int((time.perf_counter() - step_start) * 1000),
                }
            )

        ended_at = _utc_now_iso()
        duration_ms = int((time.perf_counter() - started_tick) * 1000)

        nhis_meta = _build_nhis_snapshot_meta()
        asset_meta = {
            "vehicle": _build_asset_snapshot_meta("vehicle"),
            "home": _build_asset_snapshot_meta("home"),
        }

        run_payload = {
            "last_run_at": ended_at,
            "started_at": started_at,
            "ok": bool(result_ok),
            "duration_ms": int(duration_ms),
            "step_results": steps,
            "errors": errors,
            "nhis_snapshot_id": nhis_meta.get("id"),
            "asset_snapshot_ids": {
                "vehicle": asset_meta["vehicle"].get("id"),
                "home": asset_meta["home"].get("id"),
            },
            "manifest_hash": "",
        }

        manifest = _load_json(MANIFEST_PATH, fallback={})
        manifest["refresh"] = {
            "last_run_at": ended_at,
            "ok": bool(result_ok),
            "duration_ms": int(duration_ms),
            "errors": errors,
            "step_results": steps,
            "active_snapshots": {
                "nhis": nhis_meta,
                "assets": asset_meta,
            },
        }
        manifest["active_snapshots"] = {
            "nhis": nhis_meta,
            "assets": asset_meta,
        }
        manifest_without_hash = dict(manifest)
        manifest_without_hash.pop("manifest_hash", None)
        manifest_hash = _json_hash(manifest_without_hash)
        manifest["manifest_hash"] = manifest_hash
        run_payload["manifest_hash"] = manifest_hash

        _write_json(MANIFEST_PATH, manifest)
        _write_json(RUN_LOG_PATH, run_payload)

    print("[refresh-official-snapshots]")
    print(f"ok={int(bool(result_ok))} duration_ms={duration_ms} run_log={RUN_LOG_PATH}")
    print(f"manifest={MANIFEST_PATH}")
    if errors:
        print(f"errors={';'.join(errors)}")
    return 0 if result_ok else 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공식 스냅샷 갱신 배치 실행")
    parser.add_argument("--timeout", type=int, default=10, help="refresh timeout(초)")
    parser.add_argument("--verify-timeout", type=int, default=12, help="verify timeout(초)")
    parser.add_argument("--verify-offline", action="store_true", help="공식 검증을 오프라인 스냅샷만으로 실행")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    return run_refresh(
        timeout=max(3, int(args.timeout)),
        verify_timeout=max(4, int(args.verify_timeout)),
        verify_offline=bool(args.verify_offline),
    )


if __name__ == "__main__":
    raise SystemExit(main())
