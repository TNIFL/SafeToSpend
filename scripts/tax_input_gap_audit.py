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
    out: list[dict[str, Any]] = []
    for key, count in sorted(counter.items(), key=lambda x: (-int(x[1]), str(x[0]))):
        out.append({"key": str(key), "count": int(count), "percent": _pct(int(count), int(total))})
    return out


def _parse_int_or_none(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).replace(",", "").replace("원", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def run_audit(cfg: GapConfig) -> dict[str, Any]:
    from app import create_app
    from core.time import utcnow
    from domain.models import TaxProfile, User
    from services.accuracy_reason_codes import TAX_REASON_MISSING_TAXABLE_INCOME
    from services.onboarding import TAXABLE_INCOME_ANNUAL_KEYS, evaluate_tax_required_inputs, get_tax_profile
    from services.risk import build_tax_result_meta, compute_tax_estimate

    app = create_app()
    with app.app_context():
        q = User.query.order_by(User.id.asc())
        if cfg.user_pk is not None:
            q = q.filter(User.id == int(cfg.user_pk))
        elif cfg.limit > 0:
            q = q.limit(int(cfg.limit))
        users = q.all()
        user_ids = [int(u.id) for u in users]

        rows = TaxProfile.query.filter(TaxProfile.user_pk.in_(user_ids)).all() if user_ids else []
        raw_profile_by_user: dict[int, dict[str, Any]] = {}
        for row in rows:
            raw = row.profile_json if isinstance(row.profile_json, dict) else {}
            raw_profile_by_user[int(row.user_pk)] = dict(raw)

        total = int(len(users))
        errors: Counter[str] = Counter()

        field_presence = Counter()
        classification_counter = Counter()
        blocked_source_counter = Counter()
        confidence_drop_counter = Counter()
        backfill_counter = Counter()

        blocked_missing_taxable_total = 0
        taxable_positive_total = 0

        for user in users:
            user_pk = int(user.id)
            raw_profile = dict(raw_profile_by_user.get(user_pk) or {})
            profile = get_tax_profile(user_pk)
            required_inputs = evaluate_tax_required_inputs(profile)

            taxable_income = int(required_inputs.get("taxable_income_annual_krw") or 0)
            gross = _parse_int_or_none(profile.get("annual_gross_income_krw"))
            expense = _parse_int_or_none(profile.get("annual_deductible_expense_krw"))
            income_classification = str(required_inputs.get("income_classification") or "unknown")
            has_withheld = bool(required_inputs.get("has_withheld_tax_input"))
            has_prepaid = bool(required_inputs.get("has_prepaid_tax_input"))

            if taxable_income > 0:
                taxable_positive_total += 1
                field_presence["taxable_income_present"] += 1
            else:
                field_presence["taxable_income_missing"] += 1
            if (gross or 0) > 0:
                field_presence["gross_income_present"] += 1
            if (expense or 0) > 0:
                field_presence["deductible_expense_present"] += 1
            if taxable_income <= 0 and (gross or 0) > 0:
                field_presence["gross_present_taxable_missing"] += 1
            if taxable_income <= 0 and (expense or 0) > 0:
                field_presence["expense_present_taxable_missing"] += 1
            if income_classification in {"unknown", ""}:
                field_presence["income_classification_missing"] += 1
            if not has_withheld:
                field_presence["withheld_tax_missing"] += 1
            if not has_prepaid:
                field_presence["prepaid_tax_missing"] += 1

            if taxable_income > 0:
                if not has_withheld:
                    confidence_drop_counter["missing_withheld_tax"] += 1
                if not has_prepaid:
                    confidence_drop_counter["missing_prepaid_tax"] += 1
                if income_classification in {"unknown", ""}:
                    confidence_drop_counter["missing_income_classification"] += 1

            alias_positive = False
            alias_present = False
            for key in TAXABLE_INCOME_ANNUAL_KEYS:
                if key in raw_profile:
                    alias_present = True
                parsed = _parse_int_or_none(raw_profile.get(key))
                if parsed is not None and parsed > 0:
                    alias_positive = True
                    break

            has_saved_profile = bool(profile.get("_has_saved_profile"))
            visited_step2 = int(raw_profile.get("wizard_last_step") or 0) >= 2

            try:
                est = compute_tax_estimate(user_pk=user_pk, month_key=cfg.month_key)
                meta = build_tax_result_meta(est)
                accuracy_level = str(meta.get("accuracy_level") or "limited").strip().lower()
                reason = str(meta.get("reason") or "").strip().lower()
                taxable_input_source = str(getattr(est, "taxable_income_input_source", "") or "").strip().lower()
                if reason == TAX_REASON_MISSING_TAXABLE_INCOME and accuracy_level == "blocked":
                    blocked_missing_taxable_total += 1
                    blocked_source_counter[taxable_input_source or "unknown"] += 1
                    if alias_positive:
                        classification_counter["saved_but_not_read_by_calculator"] += 1
                    elif bool(raw_profile.get("taxable_income_input_attempted")) and (not alias_present):
                        # 현재 스키마에서는 명시적 attempt 플래그가 없어 보통 0으로 남는다.
                        classification_counter["collected_but_not_saved"] += 1
                    elif visited_step2 and has_saved_profile and (not alias_present):
                        classification_counter["step2_completed_without_taxable_value"] += 1
                    elif (gross or 0) > 0:
                        classification_counter["proxy_possible_exact_not_possible"] += 1
                    elif has_saved_profile:
                        classification_counter["user_skipped_taxable_input"] += 1
                    else:
                        classification_counter["user_never_started_profile_input"] += 1
                elif reason == TAX_REASON_MISSING_TAXABLE_INCOME:
                    blocked_source_counter[taxable_input_source or "unknown"] += 1
                if taxable_income <= 0 and (gross or 0) > 0:
                    backfill_counter["gross_minus_expense_proxy_possible"] += 1
                if taxable_income <= 0 and (gross or 0) <= 0 and (expense or 0) > 0:
                    backfill_counter["expense_only_no_proxy"] += 1
            except Exception as exc:
                errors[f"{type(exc).__name__}"] += 1

        # 코드 기준으로 폼 미수집 경로는 현재 없음(입력 필드 존재)
        classification_counter.setdefault("form_not_collecting", 0)

        return {
            "generated_at_utc": utcnow().isoformat(timespec="seconds"),
            "month_key": cfg.month_key,
            "scanned_users": total,
            "blocked_missing_taxable_income": {
                "count": int(blocked_missing_taxable_total),
                "percent": _pct(int(blocked_missing_taxable_total), total),
            },
            "field_presence_summary": [
                {
                    "key": "official_taxable_income_annual_krw_present",
                    "count": int(field_presence.get("taxable_income_present", 0)),
                    "percent": _pct(int(field_presence.get("taxable_income_present", 0)), total),
                },
                {
                    "key": "annual_gross_income_present_taxable_missing",
                    "count": int(field_presence.get("gross_present_taxable_missing", 0)),
                    "percent": _pct(int(field_presence.get("gross_present_taxable_missing", 0)), total),
                },
                {
                    "key": "annual_deductible_expense_present_taxable_missing",
                    "count": int(field_presence.get("expense_present_taxable_missing", 0)),
                    "percent": _pct(int(field_presence.get("expense_present_taxable_missing", 0)), total),
                },
                {
                    "key": "income_classification_missing",
                    "count": int(field_presence.get("income_classification_missing", 0)),
                    "percent": _pct(int(field_presence.get("income_classification_missing", 0)), total),
                },
                {
                    "key": "withheld_tax_annual_krw_missing",
                    "count": int(field_presence.get("withheld_tax_missing", 0)),
                    "percent": _pct(int(field_presence.get("withheld_tax_missing", 0)), total),
                },
                {
                    "key": "prepaid_tax_annual_krw_missing",
                    "count": int(field_presence.get("prepaid_tax_missing", 0)),
                    "percent": _pct(int(field_presence.get("prepaid_tax_missing", 0)), total),
                },
            ],
            "missing_taxable_income_classification": _as_rows(
                classification_counter,
                int(max(1, blocked_missing_taxable_total)),
            ),
            "taxable_input_source_on_missing": _as_rows(
                blocked_source_counter,
                int(max(1, sum(blocked_source_counter.values()))),
            ),
            "confidence_drop_with_taxable_income": {
                "denominator_taxable_income_users": int(taxable_positive_total),
                "distribution": _as_rows(confidence_drop_counter, int(max(1, taxable_positive_total))),
            },
            "auto_backfill_candidates": {
                "distribution": _as_rows(backfill_counter, total),
                "proxy_upgrade_possible_percent": _pct(
                    int(backfill_counter.get("gross_minus_expense_proxy_possible", 0)),
                    total,
                ),
            },
            "errors": _as_rows(errors, int(max(1, sum(errors.values())))),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tax input gaps behind missing_taxable_income.")
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
