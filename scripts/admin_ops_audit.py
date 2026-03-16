from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from core import admin_guard as ag


def _print(status: str, msg: str) -> None:
    print(f"[{status}] {msg}")


def main() -> int:
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    admin_email = "qa-admin@safetospend.local"
    non_admin_email = "qa-user@safetospend.local"

    orig_current_user = ag.current_user
    orig_get_admin_emails = ag.get_admin_emails
    ag.get_admin_emails = lambda: {admin_email}

    # 익명 접근 -> 로그인 이동
    try:
        ag.current_user = lambda: None
        res = client.get("/admin", follow_redirects=False)
        if int(res.status_code) not in {301, 302}:
            _print("FAIL", f"익명 /admin expected 302, got {res.status_code}")
            return 1

        # 비관리자 -> 403
        ag.current_user = lambda: SimpleNamespace(id=101, email=non_admin_email)
        for path in ("/admin", "/admin/ops", "/admin/api/ops/summary?days=30"):
            res = client.get(path, follow_redirects=False)
            if int(res.status_code) != 403:
                _print("FAIL", f"비관리자 {path} expected 403, got {res.status_code}")
                return 1

        # 관리자 -> 200
        ag.current_user = lambda: SimpleNamespace(id=100, email=admin_email)

        for path in ("/admin", "/admin/ops"):
            res = client.get(path, follow_redirects=False)
            if int(res.status_code) != 200:
                _print("FAIL", f"관리자 {path} expected 200, got {res.status_code}")
                return 1

        res = client.get("/admin/support", follow_redirects=False)
        if int(res.status_code) not in {301, 302}:
            _print("FAIL", f"관리자 /admin/support expected redirect, got {res.status_code}")
            return 1

        res = client.get("/admin/api/ops/summary?days=30", follow_redirects=False)
        if int(res.status_code) != 200:
            _print("FAIL", f"관리자 ops summary api expected 200, got {res.status_code}")
            return 1
        payload = res.get_json(silent=True) or {}
        for key in ("latest", "series", "freshness"):
            if key not in payload:
                _print("FAIL", f"ops summary payload missing key: {key}")
                return 1
        # days 엣지케이스: 잘못된 입력도 500 없이 기본/상한으로 보정
        for raw, expected_days in (("abc", 30), ("-1", 7), ("999", 90)):
            res = client.get(f"/admin/api/ops/summary?days={raw}", follow_redirects=False)
            if int(res.status_code) != 200:
                _print("FAIL", f"ops summary days={raw} expected 200, got {res.status_code}")
                return 1
            item = res.get_json(silent=True) or {}
            got_days = int(item.get("days") or 0)
            if got_days != int(expected_days):
                _print("FAIL", f"ops summary days clamp mismatch raw={raw} got={got_days} expected={expected_days}")
                return 1
        # 최신성 카드 확장 필드 계약
        freshness = payload.get("freshness") or {}
        nhis_item = freshness.get("nhis_snapshot") or {}
        for key in ("status", "warn_reason", "fetched_at"):
            if key not in nhis_item:
                _print("FAIL", f"freshness.nhis_snapshot missing key: {key}")
                return 1
        ref_item = freshness.get("reference_watch") or {}
        for key in ("status", "warn_reason", "checked_count", "changed_count", "failing_count"):
            if key not in ref_item:
                _print("FAIL", f"freshness.reference_watch missing key: {key}")
                return 1
        refresh_item = freshness.get("official_snapshot_refresh") or {}
        for key in ("status", "warn_reason", "checked_at", "ok", "manifest_hash"):
            if key not in refresh_item:
                _print("FAIL", f"freshness.official_snapshot_refresh missing key: {key}")
                return 1
    finally:
        ag.current_user = orig_current_user
        ag.get_admin_emails = orig_get_admin_emails

    _print("PASS", "관리자/비관리자 접근 제어 점검 통과")
    _print("PASS", "운영 지표 API 응답 계약 점검 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
