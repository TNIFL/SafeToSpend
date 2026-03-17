#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import create_app
from core.extensions import db
from sqlalchemy import func

from domain.models import BankAccountLink, Transaction, User, UserBankAccount
from services.bank_accounts import (
    ensure_manual_bucket,
    fingerprint as account_fingerprint,
    get_or_create_by_fingerprint,
    last4 as account_last4,
    normalize_account_number,
)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_users(*, start_user_id: int, batch_size: int):
    q = User.query.order_by(User.id.asc())
    if start_user_id > 0:
        q = q.filter(User.id >= int(start_user_id))
    return q.limit(batch_size).all()


def run_backfill(*, dry_run: bool, batch_size: int, checkpoint_path: Path, start_user_id: int | None = None) -> int:
    checkpoint = _load_checkpoint(checkpoint_path)
    checkpoint_user_id = int(checkpoint.get("last_user_id") or 0)
    cursor = int(start_user_id or 0)
    if cursor <= 0:
        cursor = checkpoint_user_id + 1 if checkpoint_user_id > 0 else 0

    users = _iter_users(start_user_id=cursor, batch_size=batch_size)
    if not users:
        print("대상 사용자가 없습니다.")
        return 0

    created_accounts = 0
    linked_rows_updated = 0
    tx_rows_updated = 0

    for user in users:
        user_pk = int(user.id)

        links = (
            BankAccountLink.query.filter(BankAccountLink.user_pk == user_pk)
            .order_by(BankAccountLink.id.asc())
            .all()
        )
        for link in links:
            digits = normalize_account_number(link.account_number)
            fp = account_fingerprint(digits)
            l4 = account_last4(digits)
            if not fp:
                continue
            account_row = get_or_create_by_fingerprint(
                user_pk=user_pk,
                bank_code_opt=(link.bank_code or None),
                account_fingerprint=fp,
                account_last4=l4,
                alias_opt=(link.alias or None),
            )
            if not link.bank_account_id or int(link.bank_account_id) != int(account_row.id):
                linked_rows_updated += 1
                if not dry_run:
                    link.bank_account_id = int(account_row.id)
                    db.session.add(link)
            # 신규 생성 여부를 정확히 세기 어렵기 때문에, fingerprint 기준 존재 확인으로 추정 카운트
            if account_row.created_at and account_row.updated_at and account_row.created_at == account_row.updated_at:
                # same transaction에서만 의미 있는 추정치이므로 중복 집계 허용하지 않음
                pass

        # 수동 입력 거래는 기타(수동) 버킷으로 안전하게 연결
        manual_bucket_id = None
        manual_missing_q = (
            Transaction.query.filter(Transaction.user_pk == user_pk)
            .filter(Transaction.bank_account_id.is_(None))
            .filter(Transaction.source.in_(("manual", "quick", "tx_new")))
        )
        manual_missing_count = int(manual_missing_q.count() or 0)
        if manual_missing_count > 0:
            bucket = ensure_manual_bucket(user_pk)
            manual_bucket_id = int(bucket.id)
            tx_rows_updated += manual_missing_count
            if not dry_run:
                manual_missing_q.update({"bank_account_id": manual_bucket_id}, synchronize_session=False)

        # popbill 거래가 미지정이고, 사용자에게 활성 링크 계좌가 1개면 안전하게 채움
        active_link_account_ids = sorted(
            {
                int(link.bank_account_id)
                for link in links
                if bool(link.is_active) and int(link.bank_account_id or 0) > 0
            }
        )
        if len(active_link_account_ids) == 1:
            target_id = int(active_link_account_ids[0])
            popbill_missing_q = (
                Transaction.query.filter(Transaction.user_pk == user_pk)
                .filter(Transaction.bank_account_id.is_(None))
                .filter(Transaction.source == "popbill")
            )
            popbill_missing_count = int(popbill_missing_q.count() or 0)
            if popbill_missing_count > 0:
                tx_rows_updated += popbill_missing_count
                if not dry_run:
                    popbill_missing_q.update({"bank_account_id": target_id}, synchronize_session=False)

        if not dry_run:
            db.session.flush()

        checkpoint_payload = {
            "last_user_id": int(user_pk),
            "dry_run": bool(dry_run),
            "batch_size": int(batch_size),
        }
        _save_checkpoint(checkpoint_path, checkpoint_payload)

    # 생성된 계좌 수 집계(전체 기준)
    created_accounts = int(db.session.query(func.count(UserBankAccount.id)).scalar() or 0)
    unassigned_tx_count = int(
        Transaction.query.filter(Transaction.bank_account_id.is_(None)).count() or 0
    )

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    print("=== backfill_bank_accounts 결과 ===")
    print(f"dry_run: {dry_run}")
    print(f"처리 사용자 수: {len(users)}")
    print(f"생성/연결 계좌(링크 기준): {created_accounts}")
    print(f"bank_account_id 채워진 링크 수: {linked_rows_updated}")
    print(f"bank_account_id 채워진 거래 수: {tx_rows_updated}")
    print(f"미지정 거래 수: {unassigned_tx_count}")
    print(f"체크포인트: {checkpoint_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill bank_account_id for links/transactions safely")
    parser.add_argument("--dry-run", action="store_true", help="변경하지 않고 대상만 집계")
    parser.add_argument("--batch-size", type=int, default=200, help="한 번에 처리할 사용자 수")
    parser.add_argument("--start-user-id", type=int, default=0, help="지정한 사용자 ID부터 처리")
    parser.add_argument(
        "--checkpoint",
        default="data/backfill_bank_accounts_checkpoint.json",
        help="중단/재개 체크포인트 파일 경로",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app()
    with app.app_context():
        return run_backfill(
            dry_run=bool(args.dry_run),
            batch_size=max(1, min(int(args.batch_size or 200), 5000)),
            checkpoint_path=Path(str(args.checkpoint)),
            start_user_id=int(args.start_user_id or 0),
        )


if __name__ == "__main__":
    raise SystemExit(main())
