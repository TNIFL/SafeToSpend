#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import inspect

# Allow "python scripts/dev_seed_receipt_retry_case.py" from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from core.extensions import db
from domain.models import ReceiptBatch, ReceiptItem, User
from services.auth import register_user
from services.evidence_vault import store_evidence_draft_text
from services.receipt_batch import ITEM_STATUS_FAILED, recompute_batch_counts

TEST_EMAIL = "test+local@safetospend.local"
TEST_PASSWORD = "Test1234!"
KST = ZoneInfo("Asia/Seoul")


def _month_key_now_kst() -> str:
    return datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m")


def _ensure_user(email: str, password: str) -> User:
    user = User.query.filter_by(email=email).first()
    if user:
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    ok, msg = register_user(email, password)
    if not ok:
        raise RuntimeError(f"테스트 계정 생성 실패: {msg}")
    user = User.query.filter_by(email=email).first()
    if not user:
        raise RuntimeError("테스트 계정 생성 후 조회 실패")
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description="영수증 재시도 QA용 실패 항목 시드")
    parser.add_argument("--email", default=TEST_EMAIL, help="대상 계정 이메일")
    parser.add_argument("--password", default=TEST_PASSWORD, help="대상 계정 비밀번호(없으면 생성)")
    parser.add_argument("--month", default="", help="대상 월(YYYY-MM), 비우면 현재 월")
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 같은 이름 실패 항목을 지우지 않고 추가합니다.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        insp = inspect(db.engine)
        if not (insp.has_table("receipt_batches") and insp.has_table("receipt_items")):
            raise RuntimeError("receipt_batches/receipt_items 테이블이 없습니다. 먼저 마이그레이션을 적용해주세요.")

        user = _ensure_user(args.email, args.password)
        user_pk = int(user.id)
        month_key = (args.month or "").strip() or _month_key_now_kst()

        # 테스트용으로 파싱 실패를 유도하기 쉬운 텍스트(파일은 정상 존재)
        stored = store_evidence_draft_text(
            user_pk=user_pk,
            month_key=month_key,
            text="this is not a real receipt payload for parser smoke",
            filename="retryable-failed-item.txt",
        )

        batch = ReceiptBatch(
            user_pk=user_pk,
            month_key=month_key,
            status="queued",
            total_count=0,
            done_count=0,
            failed_count=0,
        )
        db.session.add(batch)
        db.session.flush()

        if not args.append:
            (
                ReceiptItem.query.filter(
                    ReceiptItem.user_pk == user_pk,
                    ReceiptItem.original_filename == "retryable-failed-item.txt",
                    ReceiptItem.status == ITEM_STATUS_FAILED,
                )
                .delete(synchronize_session=False)
            )

        item = ReceiptItem(
            batch_id=batch.id,
            user_pk=user_pk,
            file_key=stored.file_key,  # 핵심: file_key가 있어야 "재시도 가능" 상태가 됨
            original_filename=stored.original_filename,
            mime_type=stored.mime_type,
            size_bytes=int(stored.size_bytes or 0),
            sha256=stored.sha256 or None,
            status=ITEM_STATUS_FAILED,
            error_message="테스트용 실패 항목입니다. 재시도 버튼 동작 확인용",
            receipt_type="electronic",
            parsed_json=None,
        )
        db.session.add(item)
        recompute_batch_counts(batch.id)
        db.session.commit()

        print("OK: retryable failed receipt item seeded")
        print(f"email={args.email}")
        print(f"password={args.password}")
        print(f"user_id={user_pk}")
        print(f"batch_id={batch.id}")
        print(f"item_id={item.id}")
        print(f"month={month_key}")
        print(f"status_url=/dashboard/review/receipt-new/batch/{batch.id}/status?month={month_key}&focus=receipt_required&q=&limit=30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
