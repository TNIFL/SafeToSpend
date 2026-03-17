from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, inspect

from core.extensions import db
from core.time import KST, utcnow
from services.official_refs.guard import check_nhis_ready, get_official_guard_status
from domain.models import (
    ActionLog,
    AssetDatasetSnapshot,
    BankAccountLink,
    DashboardEntry,
    HoldDecision,
    ImportJob,
    NhisRateSnapshot,
    ReceiptBatch,
    ReceiptItem,
    TaxBufferLedger,
    User,
)

DEFAULT_DAYS = 30
ALLOWED_DAYS = (7, 30, 90)
MAX_DAYS = 90
STALE_DAYS = 30
FAIL_STREAK_WINDOW = 3
FAIL_STREAK_WARN_THRESHOLD = 3
REFERENCE_WATCH_STALE_HOURS = 48
_REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_WATCH_STATE_PATH = _REPO_ROOT / "data" / "reference_watch" / "status.json"
LEGACY_REFERENCE_WATCH_STATE_PATH = _REPO_ROOT / "data" / "reference_watchdog_state.json"
OFFICIAL_REFRESH_RUN_LOG_PATH = _REPO_ROOT / "data" / "official_snapshots" / "run_log.json"
OFFICIAL_MANIFEST_PATH = _REPO_ROOT / "data" / "official_snapshots" / "manifest.json"
POPBILL_BANK_GUIDES_PATH = _REPO_ROOT / "data" / "reference" / "popbill_bank_guides.json"
POPBILL_GUIDE_STALE_HOURS = 24 * 7


def clamp_days(raw: Any) -> int:
    try:
        days = int(raw or DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    if days in ALLOWED_DAYS:
        return days
    if days <= min(ALLOWED_DAYS):
        return min(ALLOWED_DAYS)
    if days >= MAX_DAYS:
        return max(ALLOWED_DAYS)
    return DEFAULT_DAYS


def _table_exists(name: str) -> bool:
    try:
        return bool(inspect(db.engine).has_table(name))
    except Exception:
        return False


def _to_date_key(raw: Any) -> str:
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d")
    text = str(raw or "").strip()
    return text[:10] if len(text) >= 10 else text


def _series_days(days: int) -> tuple[list[str], datetime, datetime]:
    today = utcnow().date()
    start_date = today - timedelta(days=days - 1)
    labels: list[str] = []
    cursor = start_date
    while cursor <= today:
        labels.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(today + timedelta(days=1), time.min)
    return labels, start_dt, end_dt


def _zero_map(labels: list[str]) -> dict[str, float]:
    return {label: 0.0 for label in labels}


def _build_dau_map(labels: list[str], start_dt: datetime, end_dt: datetime) -> tuple[dict[str, float], int]:
    """
    DAU 정의:
    - 해당 날짜에 의미있는 액션을 1회 이상 수행한 distinct 사용자 수
    - 의미있는 액션 소스(기존 테이블 재사용):
      import_jobs / receipt_batches / action_logs / tax_buffer_ledger / dashboard_entries / hold_decisions
    """
    day_map = _zero_map(labels)
    sources: list[Any] = []

    try:
        if _table_exists("import_jobs"):
            sources.append(
                db.session.query(
                    func.date(ImportJob.created_at).label("d"),
                    ImportJob.user_pk.label("u"),
                ).filter(ImportJob.created_at >= start_dt, ImportJob.created_at < end_dt)
            )
        if _table_exists("receipt_batches"):
            sources.append(
                db.session.query(
                    func.date(ReceiptBatch.created_at).label("d"),
                    ReceiptBatch.user_pk.label("u"),
                ).filter(ReceiptBatch.created_at >= start_dt, ReceiptBatch.created_at < end_dt)
            )
        if _table_exists("action_logs"):
            sources.append(
                db.session.query(
                    func.date(ActionLog.created_at).label("d"),
                    ActionLog.user_pk.label("u"),
                ).filter(ActionLog.created_at >= start_dt, ActionLog.created_at < end_dt)
            )
        if _table_exists("tax_buffer_ledger"):
            sources.append(
                db.session.query(
                    func.date(TaxBufferLedger.created_at).label("d"),
                    TaxBufferLedger.user_pk.label("u"),
                ).filter(TaxBufferLedger.created_at >= start_dt, TaxBufferLedger.created_at < end_dt)
            )
        if _table_exists("dashboard_entries"):
            sources.append(
                db.session.query(
                    func.date(DashboardEntry.created_at).label("d"),
                    DashboardEntry.user_pk.label("u"),
                ).filter(DashboardEntry.created_at >= start_dt, DashboardEntry.created_at < end_dt)
            )
        if _table_exists("hold_decisions"):
            sources.append(
                db.session.query(
                    func.date(HoldDecision.created_at).label("d"),
                    HoldDecision.user_pk.label("u"),
                ).filter(HoldDecision.created_at >= start_dt, HoldDecision.created_at < end_dt)
            )
    except Exception:
        return day_map, 0

    if not sources:
        return day_map, 0

    try:
        merged = sources[0]
        for source_query in sources[1:]:
            merged = merged.union_all(source_query)
        subq = merged.subquery()
        rows = (
            db.session.query(
                subq.c.d.label("d"),
                func.count(func.distinct(subq.c.u)).label("cnt"),
            )
            .group_by(subq.c.d)
            .all()
        )
    except Exception:
        return day_map, 0

    day_count = 0
    for day_raw, count_raw in rows:
        key = _to_date_key(day_raw)
        if key not in day_map:
            continue
        count_value = float(int(count_raw or 0))
        day_map[key] = count_value
        if count_value > 0:
            day_count += 1
    return day_map, day_count


def _build_parsing_fail_rate_map(labels: list[str], start_dt: datetime, end_dt: datetime) -> tuple[dict[str, float], int]:
    """
    파싱 실패율 정의:
    - failed / (success + failed) (일별, 0~1 비율)
    - 우선 소스: receipt_items(status=done/failed)
    - 대체 소스: receipt_batches(done_count/failed_count)
    """
    rate_map = _zero_map(labels)
    fail_map = _zero_map(labels)
    total_map = _zero_map(labels)

    try:
        if _table_exists("receipt_items"):
            rows = (
                db.session.query(
                    func.date(ReceiptItem.updated_at).label("d"),
                    func.sum(case((ReceiptItem.status == "failed", 1), else_=0)).label("failed_cnt"),
                    func.sum(case((ReceiptItem.status.in_(["done", "failed"]), 1), else_=0)).label("total_cnt"),
                )
                .filter(ReceiptItem.updated_at >= start_dt, ReceiptItem.updated_at < end_dt)
                .group_by(func.date(ReceiptItem.updated_at))
                .all()
            )
        elif _table_exists("receipt_batches"):
            rows = (
                db.session.query(
                    func.date(ReceiptBatch.updated_at).label("d"),
                    func.sum(ReceiptBatch.failed_count).label("failed_cnt"),
                    func.sum(ReceiptBatch.done_count + ReceiptBatch.failed_count).label("total_cnt"),
                )
                .filter(ReceiptBatch.updated_at >= start_dt, ReceiptBatch.updated_at < end_dt)
                .group_by(func.date(ReceiptBatch.updated_at))
                .all()
            )
        else:
            rows = []
    except Exception:
        return rate_map, 0

    total_events = 0
    for day_raw, fail_cnt, total_cnt in rows:
        key = _to_date_key(day_raw)
        if key not in rate_map:
            continue
        failed = float(int(fail_cnt or 0))
        total = float(int(total_cnt or 0))
        fail_map[key] = failed
        total_map[key] = total
        total_events += int(total)

    for key in labels:
        total = float(total_map.get(key) or 0.0)
        failed = float(fail_map.get(key) or 0.0)
        rate_map[key] = round((failed / total), 6) if total > 0 else 0.0

    return rate_map, max(0, int(total_events))


def _build_new_accounts_map(labels: list[str], start_dt: datetime, end_dt: datetime) -> tuple[dict[str, float], int]:
    day_map = _zero_map(labels)
    if not _table_exists("bank_account_links"):
        return day_map, 0

    try:
        rows = (
            db.session.query(
                func.date(BankAccountLink.created_at).label("d"),
                func.count(BankAccountLink.id).label("cnt"),
            )
            .filter(BankAccountLink.created_at >= start_dt, BankAccountLink.created_at < end_dt)
            .filter(BankAccountLink.is_active.is_(True))
            .group_by(func.date(BankAccountLink.created_at))
            .all()
        )
    except Exception:
        return day_map, 0

    total_new = 0
    for day_raw, cnt_raw in rows:
        key = _to_date_key(day_raw)
        if key not in day_map:
            continue
        count_value = float(int(cnt_raw or 0))
        day_map[key] = count_value
        total_new += int(count_value)
    return day_map, max(0, total_new)


def _build_quick_match_window_metrics(window_days: int = 7) -> dict[str, Any]:
    out = {
        "shown": 0,
        "confirmed": 0,
        "rejected": 0,
        "later": 0,
        "sample_count": 0,
        "fail_rate": 0.0,
        "status": "insufficient",
    }
    if not _table_exists("action_logs"):
        return out
    try:
        since_dt = utcnow() - timedelta(days=max(1, int(window_days)))
        rows = (
            db.session.query(ActionLog.before_state)
            .filter(ActionLog.created_at >= since_dt)
            .filter(ActionLog.action_type == "label_update")
            .limit(5000)
            .all()
        )
    except Exception:
        return out

    shown = 0
    confirmed = 0
    rejected = 0
    later = 0
    for (before_state,) in rows:
        if not isinstance(before_state, dict):
            continue
        event = str(before_state.get("metric_event") or "").strip().lower()
        if event == "quick_match_suggest_shown":
            shown += 1
        elif event == "quick_match_confirmed":
            confirmed += 1
        elif event == "quick_match_rejected":
            rejected += 1
        elif event == "quick_match_later":
            later += 1

    sample = int(confirmed + rejected)
    fail_rate = float(rejected / sample) if sample > 0 else 0.0
    status = "ok" if sample >= 10 else "insufficient"

    return {
        "shown": int(shown),
        "confirmed": int(confirmed),
        "rejected": int(rejected),
        "later": int(later),
        "sample_count": sample,
        "fail_rate": fail_rate,
        "status": status,
    }


def _format_dt(raw: datetime | None) -> str | None:
    if not isinstance(raw, datetime):
        return None
    return raw.strftime("%Y-%m-%d %H:%M")


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw or default)
    except Exception:
        return int(default)


def _parse_iso_to_kst_naive(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        # 2026-03-07T01:23:45Z 형태를 우선 처리
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt


def _load_reference_watch_state(state_path: Path | None = None) -> dict[str, Any]:
    primary = state_path or REFERENCE_WATCH_STATE_PATH
    candidates = [primary]
    if primary != LEGACY_REFERENCE_WATCH_STATE_PATH:
        candidates.append(LEGACY_REFERENCE_WATCH_STATE_PATH)

    for path in candidates:
        try:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    return {}


def _load_official_refresh_run_log(path: Path | None = None) -> dict[str, Any]:
    target = path or OFFICIAL_REFRESH_RUN_LOG_PATH
    try:
        if not target.exists():
            return {}
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _load_official_manifest(path: Path | None = None) -> dict[str, Any]:
    target = path or OFFICIAL_MANIFEST_PATH
    try:
        if not target.exists():
            return {}
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _load_popbill_guide_snapshot(path: Path | None = None) -> dict[str, Any]:
    target = path or POPBILL_BANK_GUIDES_PATH
    try:
        if not target.exists():
            return {}
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _build_official_snapshot_refresh_freshness() -> dict[str, Any]:
    run_log = _load_official_refresh_run_log()
    manifest = _load_official_manifest()
    if not run_log:
        return {
            "checked_at": None,
            "status": "warn",
            "warn_reason": "missing",
            "ok": False,
            "last_error": "run_log_missing",
            "nhis_snapshot_id": None,
            "asset_snapshot_id": None,
            "manifest_hash": "",
            "duration_ms": 0,
        }

    checked_raw = str(run_log.get("last_run_at") or "").strip()
    checked_dt = _parse_iso_to_kst_naive(checked_raw)
    run_ok = bool(run_log.get("ok"))
    errors = run_log.get("errors")
    error_list = [str(item).strip() for item in (errors or []) if str(item).strip()] if isinstance(errors, list) else []

    warn_reason = "ok"
    status = "ok"
    if not run_ok:
        status = "warn"
        warn_reason = "failure"
    elif not isinstance(checked_dt, datetime):
        status = "warn"
        warn_reason = "missing"
    else:
        age_hours = max(0.0, float((utcnow() - checked_dt).total_seconds()) / 3600.0)
        if age_hours >= float(REFERENCE_WATCH_STALE_HOURS):
            status = "warn"
            warn_reason = "stale"

    manifest_hash = str(manifest.get("manifest_hash") or run_log.get("manifest_hash") or "").strip()
    asset_snapshot_ids = run_log.get("asset_snapshot_ids") if isinstance(run_log.get("asset_snapshot_ids"), dict) else {}
    home_id = _to_int(asset_snapshot_ids.get("home"), 0)
    vehicle_id = _to_int(asset_snapshot_ids.get("vehicle"), 0)
    asset_snapshot_label = None
    if home_id > 0 or vehicle_id > 0:
        asset_snapshot_label = f"home:{home_id or '-'} / vehicle:{vehicle_id or '-'}"

    return {
        "checked_at": _format_dt(checked_dt),
        "status": status,
        "warn_reason": warn_reason,
        "ok": run_ok,
        "last_error": error_list[0] if error_list else "",
        "nhis_snapshot_id": _to_int(run_log.get("nhis_snapshot_id"), 0) or None,
        "asset_snapshot_id": asset_snapshot_label,
        "manifest_hash": manifest_hash,
        "duration_ms": _to_int(run_log.get("duration_ms"), 0),
    }


def _build_reference_watch_freshness(state_path: Path | None = None) -> dict[str, Any]:
    state = _load_reference_watch_state(state_path=state_path)
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    targets = state.get("targets") if isinstance(state.get("targets"), dict) else {}
    checked_at_raw = state.get("last_checked_at") or state.get("updated_at")
    checked_at = _parse_iso_to_kst_naive(checked_at_raw)

    checked_count = _to_int(
        state.get("checked_count"),
        _to_int(summary.get("checked_count"), 0),
    )
    changed_count = _to_int(
        state.get("changed_count"),
        _to_int(summary.get("changed_count"), 0),
    )
    failing_count = _to_int(
        state.get("failing_count"),
        _to_int(summary.get("failing_count"), 0),
    )
    max_failure_streak = _to_int(
        state.get("fail_streak"),
        _to_int(summary.get("max_failure_streak"), 0),
    )
    changed_flag = bool(state.get("changed")) or changed_count > 0
    failing_flag = bool(state.get("failing")) or failing_count > 0

    if not state or not isinstance(checked_at, datetime):
        status = "warn"
        reason = "missing"
    else:
        age_hours = max(0.0, float((utcnow() - checked_at).total_seconds()) / 3600.0)
        if age_hours >= float(REFERENCE_WATCH_STALE_HOURS):
            status = "warn"
            reason = "stale"
        elif failing_flag:
            status = "warn"
            reason = "failure"
        elif changed_flag:
            status = "warn"
            reason = "changed"
        else:
            status = "ok"
            reason = "ok"

    alerts: list[dict[str, Any]] = []
    if isinstance(targets, dict):
        for key in sorted(targets.keys()):
            row = targets.get(key)
            if not isinstance(row, dict):
                continue
            changed = bool(row.get("changed"))
            failing = bool(row.get("failing"))
            missing_patterns = [str(item) for item in list(row.get("missing_patterns") or []) if str(item).strip()]
            if not (changed or failing or missing_patterns):
                continue
            alerts.append(
                {
                    "key": str(row.get("key") or key),
                    "url": str(row.get("url") or ""),
                    "changed": changed,
                    "failing": failing,
                    "failure_reason": str(row.get("failure_reason") or ""),
                    "checked_at": str(row.get("checked_at") or ""),
                    "last_changed_at": str(row.get("last_changed_at") or ""),
                    "pattern_hit_count": _to_int(row.get("pattern_hit_count"), 0),
                    "keyword_hit_count": _to_int(row.get("keyword_hit_count"), 0),
                    "missing_patterns": missing_patterns[:5],
                    "missing_keywords": [str(item) for item in list(row.get("missing_keywords") or []) if str(item).strip()][:5],
                    "notes": [str(item) for item in list(row.get("notes") or []) if str(item).strip()][:4],
                    "focus_preview": str(row.get("focus_preview") or ""),
                }
            )
            if len(alerts) >= 8:
                break

    return {
        "checked_at": _format_dt(checked_at),
        "status": status,
        "warn_reason": reason,
        "checked_count": checked_count,
        "changed_count": changed_count,
        "failing_count": failing_count,
        "max_failure_streak": max_failure_streak,
        "alerts": alerts,
        "alert_count": len(alerts),
    }


def _build_popbill_guide_freshness(path: Path | None = None) -> dict[str, Any]:
    payload = _load_popbill_guide_snapshot(path=path)
    if not payload:
        return {
            "checked_at": None,
            "status": "warn",
            "warn_reason": "missing",
            "parsed_bank_count": 0,
            "total_bank_count": 0,
            "fallback_bank_count": 0,
            "structure_changed": True,
            "last_run_status": "fallback",
            "fetch_error": "snapshot_missing",
        }

    checked_at = _parse_iso_to_kst_naive(payload.get("updated_at"))
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    banks = payload.get("banks") if isinstance(payload.get("banks"), list) else []
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []

    parsed_bank_count = _to_int(meta.get("parsed_bank_count"), len(banks))
    total_bank_count = _to_int(meta.get("total_bank_count"), len(banks))
    if total_bank_count <= 0:
        total_bank_count = max(1, len(banks))
    fallback_bank_count = _to_int(meta.get("fallback_bank_count"), max(0, total_bank_count - parsed_bank_count))
    structure_changed = bool(meta.get("structure_changed"))
    last_run_status = str(meta.get("last_run_status") or "").strip().lower() or "ok"
    fetch_error = str(meta.get("fetch_error") or "").strip()

    if not isinstance(checked_at, datetime):
        status = "warn"
        reason = "missing"
    else:
        age_hours = max(0.0, float((utcnow() - checked_at).total_seconds()) / 3600.0)
        if age_hours >= float(POPBILL_GUIDE_STALE_HOURS):
            status = "warn"
            reason = "stale"
        elif structure_changed or parsed_bank_count <= 0:
            status = "warn"
            reason = "structure_changed"
        elif last_run_status == "partial" or fallback_bank_count > 0:
            status = "warn"
            reason = "partial"
        elif fetch_error:
            status = "warn"
            reason = "failure"
        else:
            status = "ok"
            reason = "ok"

    return {
        "checked_at": _format_dt(checked_at),
        "status": status,
        "warn_reason": reason,
        "parsed_bank_count": int(max(0, parsed_bank_count)),
        "total_bank_count": int(max(0, total_bank_count)),
        "fallback_bank_count": int(max(0, fallback_bank_count)),
        "structure_changed": bool(structure_changed),
        "last_run_status": last_run_status,
        "fetch_error": fetch_error,
        "note_count": len([x for x in notes if str(x).strip()]),
    }


def _is_nhis_refresh_failure(sources_json: Any) -> bool:
    if not isinstance(sources_json, dict):
        return False
    bootstrap = sources_json.get("bootstrap") if isinstance(sources_json.get("bootstrap"), dict) else {}
    if str(bootstrap.get("source") or "").strip().lower() == "built_in_default":
        return True
    if bool(sources_json.get("format_drift_detected")):
        return True
    for key in ("health_rate", "ltc_rate", "point_value", "policy_change", "income_rule"):
        item = sources_json.get(key)
        if isinstance(item, dict) and item.get("ok") is False:
            return True
    return False


def _is_asset_refresh_failure(payload_json: Any) -> bool:
    if not isinstance(payload_json, dict):
        return False
    if payload_json.get("official_source_allowed") is False and payload_json.get("official_adopted") is False:
        return False
    if bool(payload_json.get("format_drift_detected")):
        return True
    if payload_json.get("fetch_ok") is False:
        return True
    if str(payload_json.get("fetch_error") or "").strip():
        return True
    return False


def _recent_nhis_failure_count(window: int = FAIL_STREAK_WINDOW) -> int:
    if not _table_exists("nhis_rate_snapshots"):
        return 0
    try:
        rows = (
            NhisRateSnapshot.query.order_by(NhisRateSnapshot.fetched_at.desc(), NhisRateSnapshot.id.desc())
            .limit(max(1, int(window)))
            .all()
        )
        return int(sum(1 for row in rows if _is_nhis_refresh_failure(getattr(row, "sources_json", None))))
    except Exception:
        return 0


def _recent_asset_failure_count(dataset_key: str, window: int = FAIL_STREAK_WINDOW) -> int:
    if not _table_exists("asset_dataset_snapshots"):
        return 0
    try:
        rows = (
            AssetDatasetSnapshot.query.filter(AssetDatasetSnapshot.dataset_key == str(dataset_key))
            .order_by(AssetDatasetSnapshot.fetched_at.desc(), AssetDatasetSnapshot.id.desc())
            .limit(max(1, int(window)))
            .all()
        )
        return int(sum(1 for row in rows if _is_asset_refresh_failure(getattr(row, "payload_json", None))))
    except Exception:
        return 0


def _max_nhis_fetched_at() -> datetime | None:
    if not _table_exists("nhis_rate_snapshots"):
        return None
    try:
        return db.session.query(func.max(NhisRateSnapshot.fetched_at)).scalar()
    except Exception:
        return None


def _max_asset_updated_at(dataset_key: str) -> datetime | None:
    if not _table_exists("asset_dataset_snapshots"):
        return None
    try:
        return (
            db.session.query(func.max(AssetDatasetSnapshot.fetched_at))
            .filter(AssetDatasetSnapshot.dataset_key == str(dataset_key))
            .filter(AssetDatasetSnapshot.is_active.is_(True))
            .scalar()
        )
    except Exception:
        return None


def _freshness_status(updated_at: datetime | None, *, fail_count: int = 0) -> tuple[str, str]:
    if not isinstance(updated_at, datetime):
        return "warn", "missing"
    age_days = max(0, (utcnow() - updated_at).days)
    if int(fail_count or 0) >= FAIL_STREAK_WARN_THRESHOLD:
        return "warn", "failure"
    if age_days >= STALE_DAYS:
        return "warn", "stale"
    return "ok", "ok"


def _build_freshness() -> dict[str, Any]:
    nhis_updated = _max_nhis_fetched_at()
    property_updated = _max_asset_updated_at("home")
    car_updated = _max_asset_updated_at("vehicle")

    nhis_status, nhis_reason = _freshness_status(nhis_updated, fail_count=_recent_nhis_failure_count())
    property_status, property_reason = _freshness_status(property_updated, fail_count=_recent_asset_failure_count("home"))
    car_status, car_reason = _freshness_status(car_updated, fail_count=_recent_asset_failure_count("vehicle"))

    official_guard = get_official_guard_status()
    nhis_ready = check_nhis_ready(guard_status=official_guard)
    official_status = "ok" if bool(nhis_ready.get("ready")) else "warn"
    official_reason = str(nhis_ready.get("reason") or official_guard.get("reason") or "not_verified")

    return {
        "nhis_snapshot": {
            "fetched_at": _format_dt(nhis_updated),
            "status": nhis_status,
            "warn_reason": nhis_reason,
        },
        "property_dataset": {
            "updated_at": _format_dt(property_updated),
            "status": property_status,
            "warn_reason": property_reason,
        },
        "car_dataset": {
            "updated_at": _format_dt(car_updated),
            "status": car_status,
            "warn_reason": car_reason,
        },
        "reference_watch": _build_reference_watch_freshness(),
        "popbill_bank_guides": _build_popbill_guide_freshness(),
        "official_snapshot_refresh": _build_official_snapshot_refresh_freshness(),
        "official_registry": {
            "checked_at": str(nhis_ready.get("last_checked_at") or official_guard.get("last_checked_at") or "") or None,
            "status": official_status,
            "warn_reason": official_reason,
            "message": str(nhis_ready.get("message") or official_guard.get("message") or ""),
        },
    }


def _empty_summary(days: int, message: str) -> dict[str, Any]:
    safe_days = clamp_days(days)
    labels, _, _ = _series_days(safe_days)
    freshness = _build_freshness()
    return {
        "ok": False,
        "message": str(message or "운영 지표를 계산하지 못했어요."),
        "days": int(safe_days),
        "range": {
            "start": labels[0] if labels else None,
            "end": labels[-1] if labels else None,
        },
        "latest": {
            "dau": 0,
            "total_users": 0,
            "accounts_total": 0,
            "accounts_linked_today": 0,
            "parsing_fail_rate": 0.0,
            "quick_match_fail_rate_7d": 0.0,
            "quick_match_sample_7d": 0,
            "quick_match_confirmed_7d": 0,
            "quick_match_rejected_7d": 0,
            "quick_match_status_7d": "insufficient",
        },
        "series": {
            "labels": labels,
            "dau": [0 for _ in labels],
            "accounts_linked": [0 for _ in labels],
            "parsing_fail_rate": [0.0 for _ in labels],
        },
        "freshness": freshness,
        "has_data": {
            "dau": False,
            "accounts_linked": False,
            "parsing_fail_rate": False,
        },
    }


def build_ops_summary(days: int = DEFAULT_DAYS) -> dict[str, Any]:
    safe_days = clamp_days(days)
    try:
        labels, start_dt, end_dt = _series_days(safe_days)

        dau_map, dau_nonzero_days = _build_dau_map(labels, start_dt, end_dt)
        fail_rate_map, parsing_total_events = _build_parsing_fail_rate_map(labels, start_dt, end_dt)
        accounts_map, accounts_total_new = _build_new_accounts_map(labels, start_dt, end_dt)

        today_key = labels[-1] if labels else utcnow().strftime("%Y-%m-%d")

        try:
            total_users = int(db.session.query(func.count(User.id)).scalar() or 0) if _table_exists("users") else 0
        except Exception:
            total_users = 0

        try:
            accounts_total = (
                int(
                    db.session.query(func.count(BankAccountLink.id))
                    .filter(BankAccountLink.is_active.is_(True))
                    .scalar()
                    or 0
                )
                if _table_exists("bank_account_links")
                else 0
            )
        except Exception:
            accounts_total = 0

        freshness = _build_freshness()
        quick_match_stats = _build_quick_match_window_metrics(window_days=7)

        return {
            "ok": True,
            "message": "",
            "days": int(safe_days),
            "range": {
                "start": labels[0] if labels else None,
                "end": labels[-1] if labels else None,
            },
            "latest": {
                "dau": int(dau_map.get(today_key) or 0),
                "total_users": int(total_users),
                "accounts_total": int(accounts_total),
                "accounts_linked_today": int(accounts_map.get(today_key) or 0),
                "parsing_fail_rate": float(fail_rate_map.get(today_key) or 0.0),
                "quick_match_fail_rate_7d": float(quick_match_stats.get("fail_rate") or 0.0),
                "quick_match_sample_7d": int(quick_match_stats.get("sample_count") or 0),
                "quick_match_confirmed_7d": int(quick_match_stats.get("confirmed") or 0),
                "quick_match_rejected_7d": int(quick_match_stats.get("rejected") or 0),
                "quick_match_status_7d": str(quick_match_stats.get("status") or "insufficient"),
            },
            "series": {
                "labels": labels,
                "dau": [int(dau_map.get(label) or 0) for label in labels],
                "accounts_linked": [int(accounts_map.get(label) or 0) for label in labels],
                "parsing_fail_rate": [float(fail_rate_map.get(label) or 0.0) for label in labels],
            },
            "freshness": freshness,
            "has_data": {
                "dau": bool(dau_nonzero_days > 0),
                "accounts_linked": bool(accounts_total_new > 0),
                "parsing_fail_rate": bool(parsing_total_events > 0),
            },
        }
    except Exception as exc:
        return _empty_summary(days=safe_days, message=f"운영 지표 집계 실패: {type(exc).__name__}")
