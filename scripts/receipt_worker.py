#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from flask import current_app
from sqlalchemy import func

# Allow "python scripts/receipt_worker.py" from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from core.extensions import db
from core.time import utcnow
from domain.models import ReceiptItem
from services.receipt_batch import (
    claim_next_uploaded_item,
    mark_receipt_item_failed,
    process_receipt_item,
    requeue_stale_processing_items,
    recompute_batch_counts,
)


def _claim_one(*, stale_minutes: int = 15) -> tuple[int, int] | None:
    with db.session.begin():
        if stale_minutes > 0:
            requeue_stale_processing_items(max_age_minutes=stale_minutes, limit=50)
        item = claim_next_uploaded_item()
        if not item:
            return None
        return int(item.id), int(item.batch_id)


def _process_one(*, stale_minutes: int = 15) -> bool:
    claimed = _claim_one(stale_minutes=stale_minutes)
    if not claimed:
        return False

    item_id, batch_id = claimed
    try:
        process_receipt_item(item_id)
        recompute_batch_counts(batch_id)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        item = db.session.get(ReceiptItem, item_id)
        if item:
            _ = mark_receipt_item_failed(item, f"워커 오류: {e}")
            try:
                app_logger = current_app.logger  # type: ignore[name-defined]
                app_logger.error(
                    "[ERROR][영수증으로 거래 추가][실패] : %s, %s",
                    item.original_filename or f"item-{item_id}",
                    item.error_message or "워커 처리 실패",
                )
            except Exception:
                pass
        recompute_batch_counts(batch_id)
        db.session.commit()
        return True


def _queue_counts() -> tuple[int, int, int]:
    queued = int(
        db.session.query(func.count(ReceiptItem.id))
        .filter(ReceiptItem.status == "uploaded")
        .scalar()
        or 0
    )
    processing = int(
        db.session.query(func.count(ReceiptItem.id))
        .filter(ReceiptItem.status == "processing")
        .scalar()
        or 0
    )
    failed = int(
        db.session.query(func.count(ReceiptItem.id))
        .filter(ReceiptItem.status == "failed")
        .scalar()
        or 0
    )
    return queued, processing, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="영수증 배치 업로드 워커(순차 처리)")
    parser.add_argument("--once", action="store_true", help="현재 대기열을 모두 처리하고 종료합니다.")
    parser.add_argument("--sleep", type=float, default=2.0, help="대기열이 비었을 때 재확인 간격(초)")
    parser.add_argument("--max-items", type=int, default=0, help="최대 처리 건수(0이면 무제한)")
    parser.add_argument("--stale-minutes", type=int, default=15, help="processing 상태 복구 기준 시간(분, 0이면 비활성화)")
    parser.add_argument("--heartbeat-seconds", type=int, default=60, help="상태 로그 출력 간격(초, 0이면 비활성화)")
    parser.add_argument("--max-errors", type=int, default=10, help="연속 오류 허용 횟수(초과 시 종료, 0이면 무제한)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        processed = 0
        heartbeat_seconds = max(0, int(args.heartbeat_seconds or 0))
        last_heartbeat_at = time.monotonic()
        consecutive_errors = 0
        had_error = False
        print(
            f"[receipt-worker] start once={bool(args.once)} sleep={float(args.sleep):.1f}s "
            f"max_items={int(args.max_items)} stale_minutes={max(0, int(args.stale_minutes))} "
            f"max_errors={max(0, int(args.max_errors))}"
        )
        try:
            while True:
                now = time.monotonic()
                if heartbeat_seconds > 0 and (now - last_heartbeat_at) >= heartbeat_seconds:
                    try:
                        queued, processing, failed = _queue_counts()
                        print(
                            f"[receipt-worker] heartbeat queued={queued} processing={processing} "
                            f"failed={failed} processed_total={processed}"
                        )
                    except Exception:
                        db.session.rollback()
                    last_heartbeat_at = now

                try:
                    did_work = _process_one(stale_minutes=max(0, int(args.stale_minutes)))
                except Exception as e:
                    db.session.rollback()
                    had_error = True
                    consecutive_errors += 1
                    print(f"[receipt-worker] loop error: {str(e)[:220]}")
                    if args.once:
                        break
                    max_errors = max(0, int(args.max_errors))
                    if max_errors > 0 and consecutive_errors >= max_errors:
                        print(f"[receipt-worker] stop: too many consecutive errors ({consecutive_errors})")
                        return 1
                    backoff = min(10.0, max(0.5, float(args.sleep)) * max(1, consecutive_errors))
                    time.sleep(backoff)
                    did_work = False

                if did_work:
                    processed += 1
                    consecutive_errors = 0
                    if args.max_items > 0 and processed >= args.max_items:
                        break
                    continue

                if args.once:
                    break
                time.sleep(max(0.3, float(args.sleep)))
        except KeyboardInterrupt:
            print("[receipt-worker] stop requested (KeyboardInterrupt)")

        print(f"receipt-worker done: processed={processed}")
        if args.once and had_error and processed <= 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
