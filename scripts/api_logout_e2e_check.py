from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app import create_app
    from domain.models import User

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    email = os.getenv("E2E_TEST_EMAIL", "test+local@safetospend.local")
    password = os.getenv("E2E_TEST_PASSWORD", "Test1234!")

    try:
        with app.app_context():
            user = User.query.filter_by(email=email).first()
            if not user:
                print(
                    {
                        "ok": False,
                        "skipped": True,
                        "reason": "테스트 계정을 찾지 못했어요. scripts/dev_seed.py 실행 후 다시 시도해 주세요.",
                        "email": email,
                    }
                )
                return 0
    except Exception as exc:
        print(
            {
                "ok": False,
                "skipped": True,
                "reason": "DB 연결이 준비되지 않아 E2E 검증을 건너뜁니다.",
                "detail": str(exc),
            }
        )
        return 0

    r1 = client.post("/api/auth/token", json={"email": email, "password": password})
    j1 = r1.get_json(silent=True) or {}
    if r1.status_code != 200 or not j1.get("ok"):
        print({"ok": False, "step": "token", "status": r1.status_code, "body": j1})
        return 1

    access_token = str(j1.get("access_token") or "")
    refresh_token = str(j1.get("refresh_token") or "")
    if not access_token or not refresh_token:
        print({"ok": False, "step": "token", "reason": "토큰 발급 응답이 비어 있어요."})
        return 1

    r2 = client.post(
        "/api/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    j2 = r2.get_json(silent=True) or {}
    if r2.status_code != 200 or not j2.get("ok"):
        print({"ok": False, "step": "logout", "status": r2.status_code, "body": j2})
        return 1

    r3 = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    j3 = r3.get_json(silent=True) or {}
    blocked = r3.status_code == 401 and (not j3.get("ok", False))
    print(
        {
            "ok": bool(blocked),
            "token_status": r1.status_code,
            "logout_status": r2.status_code,
            "refresh_after_logout_status": r3.status_code,
            "refresh_after_logout_blocked": bool(blocked),
        }
    )
    return 0 if blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
