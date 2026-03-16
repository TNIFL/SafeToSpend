from __future__ import annotations

import io
from datetime import datetime, timezone

from app import app
from core.extensions import db
from domain.models import EvidenceItem, Transaction, User
from services.plan import PLAN_BASIC, PLAN_FREE, PLAN_PRO, set_user_plan


def _ensure_smoke_rows(user_id: int) -> tuple[int, int]:
    tx = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == int(user_id))
        .filter(Transaction.external_hash.like("plan-smoke-%"))
        .order_by(Transaction.id.desc())
        .first()
    )
    if not tx:
        tx = Transaction(
            user_pk=int(user_id),
            import_job_id=None,
            occurred_at=datetime(2026, 3, 1, 10, 0, 0),
            direction="out",
            amount_krw=10000,
            counterparty="플랜스모크",
            memo="plan smoke",
            source="manual",
            review_state="todo",
            external_hash=f"plan-smoke-{int(datetime.now(timezone.utc).timestamp())}",
        )
        db.session.add(tx)
        db.session.commit()

    ev = (
        db.session.query(EvidenceItem)
        .filter(EvidenceItem.user_pk == int(user_id), EvidenceItem.transaction_id == int(tx.id))
        .first()
    )
    if not ev:
        ev = EvidenceItem(
            user_pk=int(user_id),
            transaction_id=int(tx.id),
            requirement="required",
            status="missing",
            note=None,
        )
        db.session.add(ev)
        db.session.commit()

    return int(tx.id), int(ev.id)


def _set_login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = int(user_id)


def main() -> int:
    app.testing = True

    with app.app_context():
        user = db.session.query(User).order_by(User.id.asc()).first()
        if not user:
            print("FAIL: no user")
            return 1
        user_id = int(user.id)
        original_plan = str(getattr(user, "plan_code", "") or getattr(user, "plan", "free") or "free")
        original_status = str(getattr(user, "plan_status", "active") or "active")
        original_slots = int(getattr(user, "extra_account_slots", 0) or 0)
        tx_id, evidence_id = _ensure_smoke_rows(user_id)

    client = app.test_client()
    failures: list[str] = []

    def assert_status(method: str, url: str, status: int, *, data=None, content_type=None):
        if method == "GET":
            resp = client.get(url)
        else:
            resp = client.post(url, data=data, content_type=content_type)
        ok = int(resp.status_code) == int(status)
        print(("PASS" if ok else "FAIL") + f": {method} {url} -> {resp.status_code}")
        if not ok:
            failures.append(f"{method} {url} -> {resp.status_code} (expected {status})")

    try:
        for plan_code in (PLAN_FREE, PLAN_BASIC, PLAN_PRO):
            with app.app_context():
                ok, msg = set_user_plan(user_pk=user_id, plan=plan_code, status="active", extra_account_slots=0)
                if not ok:
                    failures.append(f"set_user_plan failed for {plan_code}: {msg}")
                    continue

            _set_login(client, user_id)

            assert_status("GET", "/dashboard/review?month=2026-03", 200)
            assert_status("GET", "/inbox/import", 200)
            assert_status("GET", "/dashboard/vault?month=2026-03", 200)

            assert_status(
                "POST",
                f"/inbox/evidence/{evidence_id}/mark",
                302,
                data={"status": "missing", "tab": "evidence"},
            )

            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
            assert_status(
                "POST",
                f"/inbox/evidence/{evidence_id}/upload",
                302,
                data={"tab": "evidence", "file": (io.BytesIO(png), "plan-smoke.png")},
                content_type="multipart/form-data",
            )

    finally:
        with app.app_context():
            set_user_plan(
                user_pk=user_id,
                plan=original_plan,
                status=original_status,
                extra_account_slots=original_slots,
            )

    if failures:
        print("\nFAILED:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
