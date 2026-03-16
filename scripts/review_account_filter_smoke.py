from __future__ import annotations

from app import app
from core.extensions import db
from domain.models import User


def main() -> int:
    app.testing = True
    with app.app_context():
        user = db.session.query(User).order_by(User.id.asc()).first()
        if not user:
            print("FAIL: no user")
            return 1
        user_id = int(user.id)

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id

    urls = [
        "/dashboard/review?month=2026-03",
        "/dashboard/review?month=2026-03&focus=income_confirm",
        "/dashboard/review?month=2026-03&account=5",
        "/dashboard/review?month=2026-03&focus=income_confirm&account=5",
        "/dashboard/review?month=2026-03&account=not-a-number",
        "/dashboard/review?month=2026-03&account=999999999999999999999999",
    ]

    failed = []
    for url in urls:
        resp = client.get(url)
        ok = resp.status_code == 200
        if not ok:
            failed.append(f"{url} -> {resp.status_code}")
        print(("PASS" if ok else "FAIL") + f": {url} -> {resp.status_code}")

    if failed:
        print("\nFAILED:")
        for item in failed:
            print("- " + item)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
