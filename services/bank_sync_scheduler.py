from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from flask import current_app
from sqlalchemy import text

from core.extensions import db
from core.time import KST, utcnow
from domain.models import BankAccountLink
from services.import_popbill import PopbillImportError, PopbillImportResult, sync_popbill_backfill_max_3m, sync_popbill_for_user
from services.plan import sync_interval_minutes
from services.sensitive_mask import mask_sensitive_numbers

SYNC_LOCK_NAMESPACE = 0x5A110
_LOCAL_LOCKS: set[int] = set()
_LOCAL_LOCKS_MUTEX = threading.Lock()
_LOCAL_SCHEDULER_STARTED = False
_LOCAL_SCHEDULER_MUTEX = threading.Lock()


@dataclass
class BankSyncLinkDecision:
    link_id: int
    user_pk: int
    interval_minutes: int | None
    last_synced_at: datetime | None
    due: bool
    skip_reason: str | None


@dataclass
class BankSyncLinkResult:
    link_id: int
    user_pk: int
    status: str
    reason: str = ""
    interval_minutes: int | None = None
    last_synced_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    inserted_rows: int = 0
    duplicate_rows: int = 0
    failed_rows: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    lock_acquired: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("last_synced_at", "started_at", "finished_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.isoformat(timespec="seconds")
        return payload


@dataclass
class BankSyncBatchResult:
    mode: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime
    total_links: int
    due_links: int
    processed_links: int
    success_count: int
    failed_count: int
    skipped_interval_count: int
    skipped_plan_count: int
    skipped_lock_count: int
    skipped_limit_count: int
    inserted_rows_total: int
    duplicate_rows_total: int
    failed_rows_total: int
    results: list[BankSyncLinkResult] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": str(self.mode),
            "dry_run": bool(self.dry_run),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds"),
            "total_links": int(self.total_links),
            "due_links": int(self.due_links),
            "processed_links": int(self.processed_links),
            "success_count": int(self.success_count),
            "failed_count": int(self.failed_count),
            "skipped_interval_count": int(self.skipped_interval_count),
            "skipped_plan_count": int(self.skipped_plan_count),
            "skipped_lock_count": int(self.skipped_lock_count),
            "skipped_limit_count": int(self.skipped_limit_count),
            "inserted_rows_total": int(self.inserted_rows_total),
            "duplicate_rows_total": int(self.duplicate_rows_total),
            "failed_rows_total": int(self.failed_rows_total),
            "errors": [dict(err) for err in (self.errors or [])],
            "results": [row.as_dict() for row in (self.results or [])],
        }


def _now() -> datetime:
    return utcnow()


def _to_kst_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(KST).replace(tzinfo=None)


def _mask_link_ref(link: BankAccountLink) -> str:
    bank_code = str(getattr(link, "bank_code", "") or "").strip()
    account_number = str(getattr(link, "account_number", "") or "").strip()
    tail = account_number[-4:] if len(account_number) >= 4 else "****"
    safe_tail = "".join(ch for ch in tail if ch.isdigit())[-4:] or "****"
    return f"{bank_code}-****{safe_tail}"


def _lock_key(link_id: int) -> int:
    return int((SYNC_LOCK_NAMESPACE << 20) + int(link_id))


def _is_postgres() -> bool:
    try:
        return str(db.engine.dialect.name).lower() == "postgresql"
    except Exception:
        return False


def try_acquire_bank_link_lock(link_id: int) -> bool:
    key = _lock_key(int(link_id))
    if _is_postgres():
        row = db.session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        return bool(row)
    with _LOCAL_LOCKS_MUTEX:
        if key in _LOCAL_LOCKS:
            return False
        _LOCAL_LOCKS.add(key)
        return True


def release_bank_link_lock(link_id: int) -> None:
    key = _lock_key(int(link_id))
    if _is_postgres():
        try:
            db.session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        except Exception:
            db.session.rollback()
        return
    with _LOCAL_LOCKS_MUTEX:
        _LOCAL_LOCKS.discard(key)


def _resolve_interval_minutes(user_pk: int) -> int | None:
    try:
        value = sync_interval_minutes(int(user_pk))
    except Exception:
        return None
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def list_active_links(*, user_pk: int | None = None, account_id: int | None = None) -> list[BankAccountLink]:
    q = BankAccountLink.query.filter(BankAccountLink.is_active.is_(True))
    if user_pk is not None:
        q = q.filter(BankAccountLink.user_pk == int(user_pk))
    if account_id is not None:
        q = q.filter(BankAccountLink.id == int(account_id))
    return (
        q.order_by(
            BankAccountLink.user_pk.asc(),
            BankAccountLink.id.asc(),
        ).all()
    )


def evaluate_due_links(
    links: list[BankAccountLink],
    *,
    now: datetime | None = None,
    force_run: bool = False,
) -> tuple[list[BankSyncLinkDecision], dict[str, int]]:
    now_dt = _to_kst_naive(now) or _now()
    decisions: list[BankSyncLinkDecision] = []
    summary = {
        "due": 0,
        "skipped_interval": 0,
        "skipped_plan": 0,
    }
    for link in links:
        interval = _resolve_interval_minutes(int(link.user_pk))
        last_synced = _to_kst_naive(getattr(link, "last_synced_at", None))
        due = False
        reason: str | None = None
        if force_run:
            due = True
        elif interval is None:
            reason = "plan_interval_unavailable"
            summary["skipped_plan"] += 1
        elif last_synced is None:
            due = True
        else:
            next_due = last_synced + timedelta(minutes=int(interval))
            if now_dt >= next_due:
                due = True
            else:
                reason = "interval_not_elapsed"
                summary["skipped_interval"] += 1
        if due:
            summary["due"] += 1
        decisions.append(
            BankSyncLinkDecision(
                link_id=int(link.id),
                user_pk=int(link.user_pk),
                interval_minutes=interval,
                last_synced_at=last_synced,
                due=bool(due),
                skip_reason=reason,
            )
        )
    return decisions, summary


def _run_single_link_sync(
    *,
    link: BankAccountLink,
    use_backfill_3m: bool,
) -> PopbillImportResult:
    if use_backfill_3m:
        return sync_popbill_backfill_max_3m(int(link.user_pk), link_id=int(link.id))
    return sync_popbill_for_user(int(link.user_pk), link_id=int(link.id))


def run_bank_sync_batch(
    *,
    mode: str,
    dry_run: bool = False,
    user_pk: int | None = None,
    account_id: int | None = None,
    limit: int | None = None,
    force_run: bool = False,
    use_backfill_3m: bool = False,
) -> BankSyncBatchResult:
    started_at = _now()
    links = list_active_links(user_pk=user_pk, account_id=account_id)
    decisions, due_summary = evaluate_due_links(links, force_run=bool(force_run), now=started_at)
    decision_map = {int(x.link_id): x for x in decisions}
    due_links = [link for link in links if decision_map.get(int(link.id), None) and decision_map[int(link.id)].due]
    if limit is not None and int(limit) > 0:
        due_limited = due_links[: int(limit)]
        skipped_limit_count = max(0, len(due_links) - len(due_limited))
        due_links = due_limited
    else:
        skipped_limit_count = 0

    results: list[BankSyncLinkResult] = []
    batch_errors: list[dict[str, Any]] = []
    processed_links = 0
    success_count = 0
    failed_count = 0
    skipped_lock_count = 0
    inserted_total = 0
    duplicate_total = 0
    failed_rows_total = 0

    for link in due_links:
        decision = decision_map.get(int(link.id))
        row = BankSyncLinkResult(
            link_id=int(link.id),
            user_pk=int(link.user_pk),
            status="pending",
            interval_minutes=(decision.interval_minutes if decision else None),
            last_synced_at=(decision.last_synced_at if decision else None),
            started_at=_now(),
        )
        if dry_run:
            row.status = "dry_run"
            row.reason = "due_candidate"
            row.finished_at = _now()
            processed_links += 1
            success_count += 1
            results.append(row)
            continue

        lock_acquired = False
        try:
            lock_acquired = try_acquire_bank_link_lock(int(link.id))
            row.lock_acquired = bool(lock_acquired)
            if not lock_acquired:
                row.status = "skipped_locked"
                row.reason = "link_lock_held"
                skipped_lock_count += 1
                row.finished_at = _now()
                results.append(row)
                continue

            processed_links += 1
            piece = _run_single_link_sync(link=link, use_backfill_3m=bool(use_backfill_3m))
            row.inserted_rows = int(piece.inserted_rows or 0)
            row.duplicate_rows = int(piece.duplicate_rows or 0)
            row.failed_rows = int(piece.failed_rows or 0)
            if piece.errors:
                row.errors = [dict(err) for err in piece.errors[:80] if isinstance(err, dict)]
            inserted_total += row.inserted_rows
            duplicate_total += row.duplicate_rows
            failed_rows_total += row.failed_rows
            if row.failed_rows > 0:
                row.status = "partial_failed"
                row.reason = "link_sync_partial_failure"
                failed_count += 1
                if row.errors:
                    for err in row.errors[:80]:
                        if not isinstance(err, dict):
                            continue
                        payload = {
                            "link_id": int(link.id),
                            "user_pk": int(link.user_pk),
                            "account": str(err.get("account") or _mask_link_ref(link)),
                            "error": str(err.get("error") or f"partial_failed:{int(row.failed_rows)}"),
                            "reason": str(err.get("reason") or ""),
                        }
                        batch_errors.append(payload)
                else:
                    batch_errors.append(
                        {
                            "link_id": int(link.id),
                            "user_pk": int(link.user_pk),
                            "account": _mask_link_ref(link),
                            "error": f"partial_failed:{int(row.failed_rows)}",
                            "reason": "",
                        }
                    )
            else:
                row.status = "success"
                row.reason = "ok"
                success_count += 1
        except PopbillImportError as e:
            failed_count += 1
            row.status = "error"
            row.reason = "popbill_import_error"
            safe_error = mask_sensitive_numbers(str(e) or "").strip()[:260]
            row.errors = [{"error": safe_error}]
            batch_errors.append(
                {
                    "link_id": int(link.id),
                    "user_pk": int(link.user_pk),
                    "account": _mask_link_ref(link),
                    "error": safe_error,
                }
            )
            try:
                db.session.rollback()
            except Exception:
                pass
        except Exception as e:
            failed_count += 1
            row.status = "error"
            row.reason = "unexpected_error"
            safe_error = mask_sensitive_numbers(str(e) or "").strip()[:260]
            row.errors = [{"error": safe_error}]
            batch_errors.append(
                {
                    "link_id": int(link.id),
                    "user_pk": int(link.user_pk),
                    "account": _mask_link_ref(link),
                    "error": safe_error,
                }
            )
            try:
                db.session.rollback()
            except Exception:
                pass
        finally:
            if lock_acquired:
                release_bank_link_lock(int(link.id))
            row.finished_at = _now()
            results.append(row)

    for decision in decisions:
        if decision.due:
            continue
        status = "skipped_interval" if decision.skip_reason == "interval_not_elapsed" else "skipped_plan"
        results.append(
            BankSyncLinkResult(
                link_id=int(decision.link_id),
                user_pk=int(decision.user_pk),
                status=status,
                reason=str(decision.skip_reason or ""),
                interval_minutes=decision.interval_minutes,
                last_synced_at=decision.last_synced_at,
                started_at=started_at,
                finished_at=started_at,
                lock_acquired=False,
            )
        )

    finished_at = _now()
    return BankSyncBatchResult(
        mode=str(mode),
        dry_run=bool(dry_run),
        started_at=started_at,
        finished_at=finished_at,
        total_links=int(len(links)),
        due_links=int(due_summary["due"]),
        processed_links=int(processed_links),
        success_count=int(success_count),
        failed_count=int(failed_count),
        skipped_interval_count=int(due_summary["skipped_interval"]),
        skipped_plan_count=int(due_summary["skipped_plan"]),
        skipped_lock_count=int(skipped_lock_count),
        skipped_limit_count=int(skipped_limit_count),
        inserted_rows_total=int(inserted_total),
        duplicate_rows_total=int(duplicate_total),
        failed_rows_total=int(failed_rows_total),
        results=results,
        errors=batch_errors,
    )


def run_due_bank_sync_batch(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    account_id: int | None = None,
    user_pk: int | None = None,
) -> BankSyncBatchResult:
    return run_bank_sync_batch(
        mode="auto_due",
        dry_run=bool(dry_run),
        user_pk=(int(user_pk) if user_pk is not None else None),
        account_id=(int(account_id) if account_id is not None else None),
        limit=(int(limit) if limit is not None else None),
        force_run=False,
        use_backfill_3m=False,
    )


def run_manual_bank_sync_batch(
    *,
    user_pk: int,
    use_backfill_3m: bool = False,
    link_id: int | None = None,
    dry_run: bool = False,
) -> BankSyncBatchResult:
    return run_bank_sync_batch(
        mode=("manual_backfill_3m" if use_backfill_3m else "manual"),
        dry_run=bool(dry_run),
        user_pk=int(user_pk),
        account_id=(int(link_id) if link_id is not None else None),
        limit=None,
        force_run=True,
        use_backfill_3m=bool(use_backfill_3m),
    )


def start_local_bank_sync_scheduler(app) -> None:
    global _LOCAL_SCHEDULER_STARTED

    enabled = str(os.getenv("BANK_AUTOSYNC_ENABLE_LOCAL_SCHEDULER") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return
    argv = [str(x or "").strip().lower() for x in (sys.argv or [])]
    is_probably_web_process = ("run" in argv) or any(x.endswith("app.py") for x in argv)
    if not is_probably_web_process:
        return
    app_env = str(app.config.get("APP_ENV") or "").strip().lower()
    if app_env in {"production", "prod", "staging", "stage"}:
        app.logger.info("[BANK_AUTOSYNC] local scheduler is disabled in %s", app_env or "unknown")
        return

    tick_seconds = int(os.getenv("BANK_AUTOSYNC_LOCAL_TICK_SECONDS") or 180)
    max_limit = int(os.getenv("BANK_AUTOSYNC_LOCAL_LIMIT") or 50)
    tick_seconds = max(30, tick_seconds)
    max_limit = max(1, max_limit)

    with _LOCAL_SCHEDULER_MUTEX:
        if _LOCAL_SCHEDULER_STARTED:
            return
        _LOCAL_SCHEDULER_STARTED = True

    def _run_loop() -> None:
        app.logger.info(
            "[BANK_AUTOSYNC] local scheduler started (tick=%ss, limit=%s)",
            int(tick_seconds),
            int(max_limit),
        )
        while True:
            time.sleep(float(tick_seconds))
            try:
                with app.app_context():
                    result = run_due_bank_sync_batch(dry_run=False, limit=max_limit)
                    app.logger.info(
                        "[BANK_AUTOSYNC] local tick result=%s",
                        json.dumps(
                            {
                                "due_links": int(result.due_links),
                                "processed": int(result.processed_links),
                                "success": int(result.success_count),
                                "failed": int(result.failed_count),
                                "skipped_lock": int(result.skipped_lock_count),
                            },
                            ensure_ascii=False,
                        ),
                    )
            except Exception:
                app.logger.exception("[BANK_AUTOSYNC] local scheduler tick failed")

    thread = threading.Thread(target=_run_loop, name="bank-autosync-local", daemon=True)
    thread.start()
