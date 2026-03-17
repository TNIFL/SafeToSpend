#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from services.official_refs.guard import check_nhis_ready  # noqa: E402

MANIFEST_PATH = ROOT / "data" / "official_snapshots" / "manifest.json"
RUN_LOG_PATH = ROOT / "data" / "official_snapshots" / "run_log.json"
WATCH_STATUS_PATH = ROOT / "data" / "reference_watch" / "status.json"
MAX_RUN_AGE_HOURS = 48


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    app = create_app()
    failures: list[str] = []

    with app.app_context():
        ready = check_nhis_ready()
        if not bool(ready.get("ready")):
            failures.append(f"phase1_gate_not_ready:{ready.get('reason')}")

    manifest = _load_json(MANIFEST_PATH)
    run_log = _load_json(RUN_LOG_PATH)
    watch = _load_json(WATCH_STATUS_PATH)

    if not manifest:
        failures.append("manifest_missing")
    elif str(manifest.get("manifest_hash") or "").strip() == "":
        failures.append("manifest_hash_missing")

    if not run_log:
        failures.append("run_log_missing")
    else:
        if not bool(run_log.get("ok")):
            failures.append("latest_refresh_not_ok")
        run_at = _parse_iso(run_log.get("last_run_at"))
        if run_at is None:
            failures.append("last_run_at_missing")
        else:
            age_limit = datetime.now(run_at.tzinfo) - timedelta(hours=MAX_RUN_AGE_HOURS)
            if run_at < age_limit:
                failures.append("refresh_run_too_old")

    fail_streak = int(watch.get("fail_streak") or 0)
    if fail_streak > 0:
        failures.append(f"reference_watch_fail_streak:{fail_streak}")

    if failures:
        print("[predeploy-check] FAIL")
        for item in failures:
            print(f"- {item}")
        return 1

    print("[predeploy-check] PASS")
    print(f"- manifest: {MANIFEST_PATH}")
    print(f"- run_log: {RUN_LOG_PATH}")
    print(f"- reference_watch: {WATCH_STATUS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
