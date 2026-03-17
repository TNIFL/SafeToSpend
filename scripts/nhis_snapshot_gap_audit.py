from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class GapConfig:
    month_key: str
    limit: int
    user_pk: int | None


def _norm_month_key(raw: str | None) -> str:
    text = str(raw or "").strip()
    if len(text) == 7 and text[4] == "-":
        y, m = text.split("-", 1)
        try:
            yy = int(y)
            mm = int(m)
            if 2000 <= yy <= 2100 and 1 <= mm <= 12:
                return f"{yy:04d}-{mm:02d}"
        except Exception:
            pass
    from core.time import utcnow

    return utcnow().strftime("%Y-%m")


def _pct(count: int, total: int) -> float:
    if int(total) <= 0:
        return 0.0
    return round((int(count) / int(total)) * 100.0, 2)


def _as_rows(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, count in sorted(counter.items(), key=lambda x: (-int(x[1]), str(x[0]))):
        rows.append({"key": str(key), "count": int(count), "percent": _pct(int(count), int(total))})
    return rows


def _has_number(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).replace(",", "").strip()
    if not text:
        return False
    try:
        _ = float(text)
        return True
    except Exception:
        return False


def run_audit(cfg: GapConfig) -> dict[str, Any]:
    from app import create_app
    from core.time import utcnow
    from domain.models import User
    from services.accuracy_reason_codes import NHIS_REASON_MISSING_SNAPSHOT
    from services.nhis_runtime import compute_nhis_monthly_buffer, evaluate_nhis_required_inputs
    from services.nhis_rates import ensure_active_snapshot, snapshot_to_display_dict
    from services.official_refs.guard import check_nhis_ready, get_official_guard_status

    app = create_app()
    with app.app_context():
        q = User.query.order_by(User.id.asc())
        if cfg.user_pk is not None:
            q = q.filter(User.id == int(cfg.user_pk))
        elif cfg.limit > 0:
            q = q.limit(int(cfg.limit))
        users = q.all()
        total = int(len(users))

        guard_status = get_official_guard_status()
        ready_status = check_nhis_ready(guard_status=guard_status)
        snapshot_status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6)
        snapshot_display = snapshot_to_display_dict(snapshot_status.snapshot)

        reason_counter = Counter()
        level_counter = Counter()
        root_cause_counter = Counter()
        member_type_counter = Counter()
        input_presence_counter = Counter()
        potential_if_snapshot_ready_counter = Counter()
        errors = Counter()

        for user in users:
            user_pk = int(user.id)
            try:
                _amount, _note, payload = compute_nhis_monthly_buffer(user_pk=user_pk, month_key=cfg.month_key)
                meta = dict((payload or {}).get("result_meta") or {})
                profile = dict((payload or {}).get("profile") or {})
                estimate = dict((payload or {}).get("estimate") or {})

                level = str(meta.get("accuracy_level") or "limited").strip().lower() or "limited"
                reason = str(meta.get("reason") or "unknown").strip().lower() or "unknown"
                member_type = str(profile.get("member_type") or "unknown").strip().lower() or "unknown"

                level_counter[level] += 1
                reason_counter[reason] += 1
                member_type_counter[member_type] += 1

                if member_type not in {"unknown", ""}:
                    input_presence_counter["membership_type_present"] += 1
                if _has_number(profile.get("salary_monthly_krw")):
                    input_presence_counter["salary_monthly_present"] += 1
                if _has_number(profile.get("annual_income_krw")):
                    input_presence_counter["annual_income_present"] += 1
                if _has_number(profile.get("non_salary_annual_income_krw")):
                    input_presence_counter["non_salary_income_present"] += 1
                if _has_number(profile.get("property_tax_base_total_krw")):
                    input_presence_counter["property_tax_base_present"] += 1
                if _has_number(profile.get("financial_income_annual_krw")):
                    input_presence_counter["financial_income_present"] += 1

                req_if_snapshot_ready = evaluate_nhis_required_inputs(
                    estimate=estimate,
                    profile=profile,
                    official_ready=True,
                )
                if bool(req_if_snapshot_ready.get("high_confidence_inputs_ready")):
                    potential_if_snapshot_ready_counter["high_confidence_inputs_ready"] += 1
                if bool(req_if_snapshot_ready.get("exact_ready_inputs_ready")):
                    potential_if_snapshot_ready_counter["exact_ready_inputs_ready"] += 1

                if reason == NHIS_REASON_MISSING_SNAPSHOT:
                    if not bool(ready_status.get("ready")):
                        if not bool(guard_status.get("valid")):
                            root_cause_counter[f"official_guard_not_ready:{guard_status.get('reason') or 'unknown'}"] += 1
                        else:
                            root_cause_counter[f"snapshot_pipeline_not_ready:{ready_status.get('reason') or 'unknown'}"] += 1
                    else:
                        root_cause_counter["runtime_missing_snapshot_despite_ready"] += 1
                else:
                    root_cause_counter[f"non_snapshot_reason:{reason}"] += 1
            except Exception as exc:
                errors[f"{type(exc).__name__}"] += 1

        return {
            "generated_at_utc": utcnow().isoformat(timespec="seconds"),
            "month_key": cfg.month_key,
            "scanned_users": total,
            "official_guard_status": {
                "valid": bool(guard_status.get("valid")),
                "reason": str(guard_status.get("reason") or ""),
                "message": str(guard_status.get("message") or ""),
                "last_checked_at": str(guard_status.get("last_checked_at") or ""),
                "mismatch_count": int(guard_status.get("mismatch_count") or 0),
                "network_error_count": int(guard_status.get("network_error_count") or 0),
            },
            "nhis_ready_status": {
                "ready": bool(ready_status.get("ready")),
                "reason": str(ready_status.get("reason") or ""),
                "message": str(ready_status.get("message") or ""),
                "snapshot_year": int(ready_status.get("snapshot_year") or 0),
                "property_points_table_loaded": bool(ready_status.get("property_points_table_loaded")),
            },
            "snapshot_runtime_status": {
                "snapshot_exists": bool(snapshot_status.snapshot is not None),
                "is_stale": bool(snapshot_status.is_stale),
                "is_fallback_default": bool(snapshot_status.is_fallback_default),
                "update_error": str(snapshot_status.update_error or ""),
                "effective_year": int(snapshot_display.get("effective_year") or 0),
                "fetched_at": (
                    snapshot_display.get("fetched_at").isoformat(timespec="seconds")
                    if snapshot_display.get("fetched_at")
                    else ""
                ),
            },
            "accuracy_level_distribution": _as_rows(level_counter, total),
            "reason_distribution": _as_rows(reason_counter, total),
            "blocked_root_cause_distribution": _as_rows(
                root_cause_counter,
                int(max(1, sum(root_cause_counter.values()))),
            ),
            "membership_and_input_presence": [
                {
                    "key": "membership_type_present",
                    "count": int(input_presence_counter.get("membership_type_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("membership_type_present", 0)), total),
                },
                {
                    "key": "salary_monthly_krw_present",
                    "count": int(input_presence_counter.get("salary_monthly_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("salary_monthly_present", 0)), total),
                },
                {
                    "key": "annual_income_krw_present",
                    "count": int(input_presence_counter.get("annual_income_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("annual_income_present", 0)), total),
                },
                {
                    "key": "non_salary_annual_income_krw_present",
                    "count": int(input_presence_counter.get("non_salary_income_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("non_salary_income_present", 0)), total),
                },
                {
                    "key": "property_tax_base_total_krw_present",
                    "count": int(input_presence_counter.get("property_tax_base_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("property_tax_base_present", 0)), total),
                },
                {
                    "key": "financial_income_annual_krw_present",
                    "count": int(input_presence_counter.get("financial_income_present", 0)),
                    "percent": _pct(int(input_presence_counter.get("financial_income_present", 0)), total),
                },
            ],
            "member_type_distribution": _as_rows(member_type_counter, total),
            "potential_if_snapshot_ready": {
                "high_confidence_inputs_ready_count": int(
                    potential_if_snapshot_ready_counter.get("high_confidence_inputs_ready", 0)
                ),
                "high_confidence_inputs_ready_percent": _pct(
                    int(potential_if_snapshot_ready_counter.get("high_confidence_inputs_ready", 0)),
                    total,
                ),
                "exact_ready_inputs_ready_count": int(
                    potential_if_snapshot_ready_counter.get("exact_ready_inputs_ready", 0)
                ),
                "exact_ready_inputs_ready_percent": _pct(
                    int(potential_if_snapshot_ready_counter.get("exact_ready_inputs_ready", 0)),
                    total,
                ),
            },
            "errors": _as_rows(errors, int(max(1, sum(errors.values())))),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit NHIS missing_snapshot root causes and input gaps.")
    parser.add_argument("--month", dest="month_key", default="", help="Target month key (YYYY-MM)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of users to scan (0 = all)")
    parser.add_argument("--user-pk", type=int, default=0, help="Single user id to scan")
    parser.add_argument("--output", default="", help="Optional output json file path")
    args = parser.parse_args()

    cfg = GapConfig(
        month_key=_norm_month_key(args.month_key),
        limit=max(0, int(args.limit or 0)),
        user_pk=(int(args.user_pk) if int(args.user_pk or 0) > 0 else None),
    )

    try:
        payload = run_audit(cfg)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "hint": "DB 연결/환경변수(SQLALCHEMY_DATABASE_URI 또는 DATABASE_URL) 상태를 확인하세요.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
