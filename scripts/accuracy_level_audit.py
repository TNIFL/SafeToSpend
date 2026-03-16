from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class AuditConfig:
    month_key: str
    limit: int
    user_pk: int | None
    recent_active_days: int
    legacy_days: int


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


def _as_dict(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, count in sorted(counter.items(), key=lambda x: (-int(x[1]), str(x[0]))):
        rows.append(
            {
                "key": str(key),
                "count": int(count),
                "percent": _pct(int(count), int(total)),
            }
        )
    return rows


def _segment_to_rows(
    segment_counter: dict[str, Counter[str]],
    *,
    level_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment_value, levels in sorted(segment_counter.items(), key=lambda x: str(x[0])):
        total = int(sum(int(v) for v in levels.values()))
        entry = {
            "segment": str(segment_value),
            "total": total,
            "levels": [],
        }
        for key in level_keys:
            count = int(levels.get(key, 0))
            entry["levels"].append(
                {
                    "key": str(key),
                    "count": count,
                    "percent": _pct(count, total),
                }
            )
        rows.append(entry)
    return rows


def _normalize_level(raw: str | None) -> str:
    level = str(raw or "").strip().lower()
    if level in {"exact_ready", "high_confidence", "limited", "blocked"}:
        return level
    return "limited"


def _normalize_reason(raw: str | None) -> str:
    reason = str(raw or "").strip().lower()
    return reason or "unknown"


def _is_test_email(email: str | None) -> bool:
    text = str(email or "").strip().lower()
    if not text:
        return False
    tokens = (
        "test",
        "qa",
        "dummy",
        "sample",
        "example",
        "dev",
        "local",
        "no-reply",
        "noreply",
    )
    if any(token in text for token in tokens):
        return True
    domains = ("@safetospend.local", "@localhost", "@example.com", "@example.org", "@example.net")
    return any(text.endswith(domain) for domain in domains)


def _profile_started_flag(tax_profile: dict[str, Any], nhis_profile: dict[str, Any]) -> bool:
    tax = dict(tax_profile or {})
    nhis = dict(nhis_profile or {})

    if bool(tax.get("_has_saved_profile")):
        return True

    tax_keys = (
        "industry_group",
        "tax_type",
        "prev_income_band",
        "withholding_3_3",
        "income_classification",
        "official_taxable_income_annual_krw",
        "annual_gross_income_krw",
        "annual_deductible_expense_krw",
        "withheld_tax_annual_krw",
        "prepaid_tax_annual_krw",
    )
    for key in tax_keys:
        value = tax.get(key)
        if value in (None, "", "unknown"):
            continue
        return True

    member_type = str(nhis.get("member_type") or "").strip().lower()
    if member_type in {"regional", "employee", "dependent"}:
        return True

    nhis_keys = (
        "salary_monthly_krw",
        "annual_income_krw",
        "non_salary_annual_income_krw",
        "property_tax_base_total_krw",
        "financial_income_annual_krw",
    )
    for key in nhis_keys:
        value = nhis.get(key)
        if value is None:
            continue
        text = str(value).strip().replace(",", "")
        if not text:
            continue
        try:
            if float(text) >= 0:
                return True
        except Exception:
            continue
    return False


def _aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total_users = int(len(records))
    tax_levels: Counter[str] = Counter()
    tax_reasons: Counter[str] = Counter()
    tax_reason_limited_blocked: Counter[str] = Counter()
    nhis_levels: Counter[str] = Counter()
    nhis_reasons: Counter[str] = Counter()
    nhis_reason_limited_blocked: Counter[str] = Counter()

    seg_tax_income_class: dict[str, Counter[str]] = defaultdict(Counter)
    seg_tax_profile_complete: dict[str, Counter[str]] = defaultdict(Counter)
    seg_tax_linked: dict[str, Counter[str]] = defaultdict(Counter)

    seg_nhis_member_type: dict[str, Counter[str]] = defaultdict(Counter)
    seg_nhis_profile_complete: dict[str, Counter[str]] = defaultdict(Counter)
    seg_nhis_linked: dict[str, Counter[str]] = defaultdict(Counter)

    for row in records:
        tax_level = _normalize_level(row.get("tax_level"))
        tax_reason = _normalize_reason(row.get("tax_reason"))
        nhis_level = _normalize_level(row.get("nhis_level"))
        nhis_reason = _normalize_reason(row.get("nhis_reason"))

        income_classification = str(row.get("income_classification") or "unknown").strip().lower() or "unknown"
        member_type = str(row.get("member_type") or "unknown").strip().lower() or "unknown"
        profile_segment = "profile_complete" if bool(row.get("profile_complete")) else "profile_incomplete"
        linked_segment = "linked" if bool(row.get("has_linked_account")) else "not_linked"

        tax_levels[tax_level] += 1
        tax_reasons[tax_reason] += 1
        if tax_level in {"limited", "blocked"}:
            tax_reason_limited_blocked[tax_reason] += 1
        seg_tax_income_class[income_classification][tax_level] += 1
        seg_tax_profile_complete[profile_segment][tax_level] += 1
        seg_tax_linked[linked_segment][tax_level] += 1

        nhis_levels[nhis_level] += 1
        nhis_reasons[nhis_reason] += 1
        if nhis_level in {"limited", "blocked"}:
            nhis_reason_limited_blocked[nhis_reason] += 1
        seg_nhis_member_type[member_type][nhis_level] += 1
        seg_nhis_profile_complete[profile_segment][nhis_level] += 1
        seg_nhis_linked[linked_segment][nhis_level] += 1

    tax_level_keys = ("exact_ready", "high_confidence", "limited", "blocked")
    nhis_level_keys = ("exact_ready", "high_confidence", "limited", "blocked")
    tax_limited_total = int(tax_levels.get("limited", 0) + tax_levels.get("blocked", 0))
    nhis_limited_total = int(nhis_levels.get("limited", 0) + nhis_levels.get("blocked", 0))

    return {
        "scanned_users": total_users,
        "tax": {
            "accuracy_level_distribution": _as_dict(tax_levels, total_users),
            "reason_distribution_all": _as_dict(tax_reasons, total_users),
            "reason_distribution_limited_blocked": _as_dict(tax_reason_limited_blocked, max(1, tax_limited_total)),
            "segments": {
                "income_classification": _segment_to_rows(seg_tax_income_class, level_keys=tax_level_keys),
                "linked_account": _segment_to_rows(seg_tax_linked, level_keys=tax_level_keys),
                "profile_completion": _segment_to_rows(seg_tax_profile_complete, level_keys=tax_level_keys),
            },
        },
        "nhis": {
            "accuracy_level_distribution": _as_dict(nhis_levels, total_users),
            "reason_distribution_all": _as_dict(nhis_reasons, total_users),
            "reason_distribution_limited_blocked": _as_dict(nhis_reason_limited_blocked, max(1, nhis_limited_total)),
            "segments": {
                "member_type": _segment_to_rows(seg_nhis_member_type, level_keys=nhis_level_keys),
                "linked_account": _segment_to_rows(seg_nhis_linked, level_keys=nhis_level_keys),
                "profile_completion": _segment_to_rows(seg_nhis_profile_complete, level_keys=nhis_level_keys),
            },
        },
    }


def run_audit(cfg: AuditConfig) -> dict[str, Any]:
    from app import create_app
    from core.admin_guard import is_admin_user
    from core.time import utcnow
    from domain.models import ActionLog, BankAccountLink, ImportJob, Transaction, User, UserBankAccount
    from services.nhis_runtime import compute_nhis_monthly_buffer
    from services.onboarding import get_tax_profile, tax_profile_is_complete
    from services.risk import build_tax_result_meta, compute_tax_estimate

    app = create_app()
    with app.app_context():
        now_utc = utcnow()
        active_cutoff = now_utc - timedelta(days=max(1, int(cfg.recent_active_days)))
        legacy_cutoff = now_utc - timedelta(days=max(30, int(cfg.legacy_days)))

        q = User.query.order_by(User.id.asc())
        if cfg.user_pk is not None:
            q = q.filter(User.id == int(cfg.user_pk))
        elif cfg.limit > 0:
            q = q.limit(int(cfg.limit))
        users = q.all()

        scanned_user_ids = [int(u.id) for u in users]
        if scanned_user_ids:
            recent_tx_ids = {
                int(uid)
                for (uid,) in (
                    Transaction.query.with_entities(Transaction.user_pk)
                    .filter(Transaction.user_pk.in_(scanned_user_ids))
                    .filter(Transaction.occurred_at >= active_cutoff)
                    .distinct()
                    .all()
                )
            }
            recent_action_ids = {
                int(uid)
                for (uid,) in (
                    ActionLog.query.with_entities(ActionLog.user_pk)
                    .filter(ActionLog.user_pk.in_(scanned_user_ids))
                    .filter(ActionLog.created_at >= active_cutoff)
                    .distinct()
                    .all()
                )
            }
            recent_import_ids = {
                int(uid)
                for (uid,) in (
                    ImportJob.query.with_entities(ImportJob.user_pk)
                    .filter(ImportJob.user_pk.in_(scanned_user_ids))
                    .filter(ImportJob.created_at >= active_cutoff)
                    .distinct()
                    .all()
                )
            }
            linked_ids = {
                int(uid)
                for (uid,) in (
                    BankAccountLink.query.with_entities(BankAccountLink.user_pk)
                    .filter(BankAccountLink.user_pk.in_(scanned_user_ids))
                    .filter(BankAccountLink.is_active.is_(True))
                    .distinct()
                    .all()
                )
            }
            linked_ids.update(
                int(uid)
                for (uid,) in (
                    UserBankAccount.query.with_entities(UserBankAccount.user_pk)
                    .filter(UserBankAccount.user_pk.in_(scanned_user_ids))
                    .distinct()
                    .all()
                )
            )
        else:
            recent_tx_ids = set()
            recent_action_ids = set()
            recent_import_ids = set()
            linked_ids = set()

        recent_activity_ids = set().union(recent_tx_ids, recent_action_ids, recent_import_ids)
        errors: Counter[str] = Counter()
        records: list[dict[str, Any]] = []

        for user in users:
            user_pk = int(user.id)
            tax_level = "limited"
            tax_reason = "unknown"
            nhis_level = "limited"
            nhis_reason = "unknown"
            income_classification = "unknown"
            member_type = "unknown"
            profile_complete = False

            tax_profile = get_tax_profile(user_pk)
            profile_complete = bool(tax_profile_is_complete(user_pk))
            income_classification = str(tax_profile.get("income_classification") or "unknown").strip().lower() or "unknown"

            nhis_profile: dict[str, Any] = {}
            try:
                tax_est = compute_tax_estimate(user_pk=user_pk, month_key=cfg.month_key)
                tax_meta = build_tax_result_meta(tax_est)
                tax_level = _normalize_level(tax_meta.get("accuracy_level"))
                tax_reason = _normalize_reason(tax_meta.get("reason"))
            except Exception as exc:
                tax_level = "limited"
                tax_reason = f"error:{type(exc).__name__}".lower()
                errors[f"tax:{type(exc).__name__}"] += 1

            try:
                _amount, _note, payload = compute_nhis_monthly_buffer(user_pk=user_pk, month_key=cfg.month_key)
                meta = dict((payload or {}).get("result_meta") or {})
                nhis_profile = dict((payload or {}).get("profile") or {})
                member_type = str(nhis_profile.get("member_type") or "unknown").strip().lower()
                if member_type not in {"regional", "employee", "dependent", "unknown"}:
                    member_type = "unknown"
                nhis_level = _normalize_level(meta.get("accuracy_level"))
                nhis_reason = _normalize_reason(meta.get("reason"))
            except Exception as exc:
                nhis_level = "limited"
                nhis_reason = f"error:{type(exc).__name__}".lower()
                errors[f"nhis:{type(exc).__name__}"] += 1

            is_admin = bool(is_admin_user(user))
            is_test_account = _is_test_email(getattr(user, "email", None))
            is_recent_active = bool(user_pk in recent_activity_ids)
            has_linked_account = bool(user_pk in linked_ids)
            profile_started = _profile_started_flag(tax_profile, nhis_profile)
            created_at = getattr(user, "created_at", None)
            legacy_dormant = bool(
                isinstance(created_at, datetime)
                and created_at <= legacy_cutoff
                and (not is_recent_active)
                and (not profile_started)
                and (not has_linked_account)
            )
            is_inactive = not bool(is_recent_active)

            records.append(
                {
                    "user_pk": user_pk,
                    "tax_level": tax_level,
                    "tax_reason": tax_reason,
                    "nhis_level": nhis_level,
                    "nhis_reason": nhis_reason,
                    "income_classification": income_classification,
                    "member_type": member_type,
                    "profile_complete": profile_complete,
                    "profile_started": profile_started,
                    "has_linked_account": has_linked_account,
                    "is_admin": is_admin,
                    "is_test_account": is_test_account,
                    "is_recent_active": is_recent_active,
                    "is_inactive": is_inactive,
                    "is_legacy_dormant": legacy_dormant,
                }
            )

        def _pick(fn: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
            return [row for row in records if bool(fn(row))]

        cohorts = {
            "all_users": _aggregate_records(_pick(lambda _r: True)),
            "recent_active_users": _aggregate_records(_pick(lambda r: bool(r.get("is_recent_active")))),
            "profile_started_users": _aggregate_records(_pick(lambda r: bool(r.get("profile_started")))),
            "linked_account_users": _aggregate_records(_pick(lambda r: bool(r.get("has_linked_account")))),
            "exclude_admin_test_inactive_legacy": _aggregate_records(
                _pick(
                    lambda r: (
                        (not bool(r.get("is_admin")))
                        and (not bool(r.get("is_test_account")))
                        and (not bool(r.get("is_inactive")))
                        and (not bool(r.get("is_legacy_dormant")))
                    )
                )
            ),
            "operational_target_users": _aggregate_records(
                _pick(
                    lambda r: (
                        (not bool(r.get("is_admin")))
                        and (not bool(r.get("is_test_account")))
                        and bool(r.get("is_recent_active"))
                        and bool(r.get("profile_started"))
                    )
                )
            ),
        }

        all_agg = dict(cohorts.get("all_users") or {})
        flag_counts = {
            "admin_accounts": int(sum(1 for r in records if bool(r.get("is_admin")))),
            "test_accounts": int(sum(1 for r in records if bool(r.get("is_test_account")))),
            "inactive_accounts": int(sum(1 for r in records if bool(r.get("is_inactive")))),
            "legacy_dormant_accounts": int(sum(1 for r in records if bool(r.get("is_legacy_dormant")))),
            "recent_active_accounts": int(sum(1 for r in records if bool(r.get("is_recent_active")))),
            "profile_started_accounts": int(sum(1 for r in records if bool(r.get("profile_started")))),
            "linked_account_accounts": int(sum(1 for r in records if bool(r.get("has_linked_account")))),
        }

        return {
            "generated_at_utc": now_utc.isoformat(timespec="seconds"),
            "month_key": cfg.month_key,
            "scanned_users": int(len(records)),
            "config": {
                "recent_active_days": int(cfg.recent_active_days),
                "legacy_days": int(cfg.legacy_days),
            },
            "cohort_definitions": [
                {"key": "all_users", "description": "조회된 전체 사용자"},
                {"key": "recent_active_users", "description": f"최근 {cfg.recent_active_days}일 활동 사용자"},
                {"key": "profile_started_users", "description": "세금/건보 입력이 최소 1개 이상 시작된 사용자"},
                {"key": "linked_account_users", "description": "계좌 연동 이력이 있는 사용자"},
                {
                    "key": "exclude_admin_test_inactive_legacy",
                    "description": "관리자/테스트/비활성/레거시 휴면 계정을 제외한 사용자",
                },
                {
                    "key": "operational_target_users",
                    "description": "권장 분모(관리자·테스트 제외 + 최근 활성 + 입력 시작 사용자)",
                },
            ],
            "cohort_flag_counts": flag_counts,
            "recommended_distribution_cohort": "operational_target_users",
            "errors": {
                "error_count": int(sum(errors.values())),
                "top_error_types": _as_dict(errors, int(max(1, sum(errors.values())))),
            },
            "cohorts": cohorts,
            "tax": dict(all_agg.get("tax") or {}),
            "nhis": dict(all_agg.get("nhis") or {}),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tax/NHIS accuracy_level distribution from user cohorts.")
    parser.add_argument("--month", dest="month_key", default="", help="Target month key (YYYY-MM)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of users to scan (0 = all)")
    parser.add_argument("--user-pk", type=int, default=0, help="Single user id to scan")
    parser.add_argument("--recent-active-days", type=int, default=90, help="Recent active window in days")
    parser.add_argument("--legacy-days", type=int, default=365, help="Legacy dormant threshold days")
    parser.add_argument("--output", default="", help="Optional output json file path")
    args = parser.parse_args()

    cfg = AuditConfig(
        month_key=_norm_month_key(args.month_key),
        limit=max(0, int(args.limit or 0)),
        user_pk=(int(args.user_pk) if int(args.user_pk or 0) > 0 else None),
        recent_active_days=max(1, int(args.recent_active_days or 90)),
        legacy_days=max(30, int(args.legacy_days or 365)),
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
