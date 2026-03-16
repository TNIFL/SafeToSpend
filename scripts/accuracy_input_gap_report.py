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
    if total <= 0:
        return 0.0
    return round((int(count) / int(total)) * 100.0, 2)


def _bucket_from_meta(meta: dict[str, Any]) -> str:
    accuracy_level = str(meta.get("accuracy_level") or "limited").strip().lower()
    auto = bool(meta.get("auto_fillable_fields") or [])
    low = bool(meta.get("low_confidence_inferable_fields") or [])
    user = bool(meta.get("needs_user_input_fields") or [])

    if accuracy_level in {"exact_ready", "high_confidence"}:
        return "already_high_or_exact"
    if (not user) and (auto or low):
        return "auto_upgrade_possible"
    if user and (auto or low):
        return "mixed_requires_user_input"
    if user:
        return "user_input_required"
    if low:
        return "low_confidence_inference_only"
    return "no_clear_path"


def _collect_fields(meta: dict[str, Any], *, key: str) -> list[str]:
    raw = list(meta.get(key) or [])
    out: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def _as_rows(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, v in sorted(counter.items(), key=lambda x: (-int(x[1]), str(x[0]))):
        rows.append({"key": str(k), "count": int(v), "percent": _pct(int(v), int(total))})
    return rows


def run_report(cfg: GapConfig) -> dict[str, Any]:
    from app import create_app
    from core.time import utcnow
    from domain.models import User
    from services.nhis_runtime import compute_nhis_monthly_buffer
    from services.risk import build_tax_result_meta, compute_tax_estimate

    app = create_app()
    with app.app_context():
        q = User.query.order_by(User.id.asc())
        if cfg.user_pk is not None:
            q = q.filter(User.id == int(cfg.user_pk))
        elif cfg.limit > 0:
            q = q.limit(int(cfg.limit))
        users = q.all()

        total = int(len(users))
        tax_bucket_counter: Counter[str] = Counter()
        nhis_bucket_counter: Counter[str] = Counter()
        tax_user_required_counter: Counter[str] = Counter()
        nhis_user_required_counter: Counter[str] = Counter()
        tax_auto_counter: Counter[str] = Counter()
        nhis_auto_counter: Counter[str] = Counter()
        tax_low_counter: Counter[str] = Counter()
        nhis_low_counter: Counter[str] = Counter()
        errors: Counter[str] = Counter()

        for user in users:
            user_pk = int(user.id)
            try:
                tax_meta = build_tax_result_meta(compute_tax_estimate(user_pk=user_pk, month_key=cfg.month_key))
                bucket = _bucket_from_meta(tax_meta)
                tax_bucket_counter[bucket] += 1
                for key in _collect_fields(tax_meta, key="needs_user_input_fields"):
                    tax_user_required_counter[key] += 1
                for key in _collect_fields(tax_meta, key="auto_fillable_fields"):
                    tax_auto_counter[key] += 1
                for key in _collect_fields(tax_meta, key="low_confidence_inferable_fields"):
                    tax_low_counter[key] += 1
            except Exception as exc:
                errors[f"tax:{type(exc).__name__}"] += 1

            try:
                _amount, _note, payload = compute_nhis_monthly_buffer(user_pk=user_pk, month_key=cfg.month_key)
                nhis_meta = dict((payload or {}).get("result_meta") or {})
                bucket = _bucket_from_meta(nhis_meta)
                nhis_bucket_counter[bucket] += 1
                for key in _collect_fields(nhis_meta, key="needs_user_input_fields"):
                    nhis_user_required_counter[key] += 1
                for key in _collect_fields(nhis_meta, key="auto_fillable_fields"):
                    nhis_auto_counter[key] += 1
                for key in _collect_fields(nhis_meta, key="low_confidence_inferable_fields"):
                    nhis_low_counter[key] += 1
            except Exception as exc:
                errors[f"nhis:{type(exc).__name__}"] += 1

        return {
            "generated_at_utc": utcnow().isoformat(timespec="seconds"),
            "month_key": cfg.month_key,
            "scanned_users": total,
            "tax": {
                "gap_bucket_distribution": _as_rows(tax_bucket_counter, total),
                "needs_user_input_top_fields": _as_rows(tax_user_required_counter, total),
                "auto_fillable_top_fields": _as_rows(tax_auto_counter, total),
                "low_confidence_inferable_top_fields": _as_rows(tax_low_counter, total),
                "auto_upgrade_possible_percent": _pct(int(tax_bucket_counter.get("auto_upgrade_possible", 0)), total),
            },
            "nhis": {
                "gap_bucket_distribution": _as_rows(nhis_bucket_counter, total),
                "needs_user_input_top_fields": _as_rows(nhis_user_required_counter, total),
                "auto_fillable_top_fields": _as_rows(nhis_auto_counter, total),
                "low_confidence_inferable_top_fields": _as_rows(nhis_low_counter, total),
                "auto_upgrade_possible_percent": _pct(int(nhis_bucket_counter.get("auto_upgrade_possible", 0)), total),
            },
            "errors": _as_rows(errors, max(1, sum(errors.values()))),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit input gaps and auto-fill upgrade potential.")
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
        payload = run_report(cfg)
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
