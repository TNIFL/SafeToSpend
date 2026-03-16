from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from services.privacy_guards import (
    make_identifier_storage_token,
    redact_identifier_for_render,
    sanitize_account_like_value,
)


def _sanitize_job_error_summary(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_name = str(key or "").strip().lower()
            if key_name == "account":
                cleaned[key] = _sanitize_job_error_summary(item)
            else:
                cleaned[key] = _sanitize_job_error_summary(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_job_error_summary(item) for item in value]
    if isinstance(value, str) and value.count("-") == 1:
        left, right = value.split("-", 1)
        if left.isdigit():
            return f"{left}-{redact_identifier_for_render(right)}"
    return value


def _ensure_user_bank_account(
    *,
    user_pk: int,
    bank_code: str | None,
    identifier: str,
    alias: str | None,
    accounts_by_key: dict[tuple[int, str], UserBankAccount],
    apply: bool,
) -> tuple[UserBankAccount, bool]:
    safe = sanitize_account_like_value(identifier)
    key = (int(user_pk), safe.hashed)
    existing = accounts_by_key.get(key)
    created = False
    if existing:
        if apply:
            if bank_code and not existing.bank_code:
                existing.bank_code = bank_code
            if safe.last4 and not existing.account_last4:
                existing.account_last4 = safe.last4
            if alias and not existing.alias:
                existing.alias = alias
        return existing, created

    account = UserBankAccount(
        user_pk=int(user_pk),
        bank_code=bank_code or None,
        account_fingerprint=safe.hashed,
        account_last4=safe.last4 or None,
        alias=alias or "연동 계좌",
    )
    if apply:
        db.session.add(account)
        db.session.flush()
    accounts_by_key[key] = account
    created = True
    return account, created


def run_remediation(*, apply: bool, limit: int | None, output_path: Path) -> dict:
    from app import create_app
    from core.extensions import db
    from domain.models import BankAccountLink, ImportJob, UserBankAccount

    scanned_rows = {"bank_account_links": 0, "import_jobs": 0}
    changed_rows = {"bank_account_links": 0, "import_jobs": 0, "user_bank_accounts_created": 0}
    skipped_rows = {"bank_account_links": 0, "import_jobs": 0}
    affected_models: set[str] = set()

    accounts_by_key = {
        (int(row.user_pk), str(row.account_fingerprint)): row
        for row in UserBankAccount.query.filter(UserBankAccount.account_fingerprint.isnot(None)).all()
    }

    links_query = BankAccountLink.query.order_by(BankAccountLink.id.asc())
    if limit:
        links_query = links_query.limit(int(limit))
    for link in links_query.all():
        scanned_rows["bank_account_links"] += 1
        raw_value = str(link.account_number or "").strip()
        if not raw_value or raw_value.startswith("acct_"):
            continue

        safe = sanitize_account_like_value(raw_value)
        if not safe.hashed:
            skipped_rows["bank_account_links"] += 1
            continue

        account = None
        if link.bank_account_id:
            account = UserBankAccount.query.filter_by(id=int(link.bank_account_id)).first()
        if account is None:
            account, created = _ensure_user_bank_account(
                user_pk=int(link.user_pk),
                bank_code=str(link.bank_code or "").strip() or None,
                identifier=raw_value,
                alias=link.alias,
                accounts_by_key=accounts_by_key,
                apply=apply,
            )
            if created:
                changed_rows["user_bank_accounts_created"] += 1
                affected_models.add("UserBankAccount")

        changed = False
        token = make_identifier_storage_token(safe.normalized_digits or raw_value, prefix="acct")
        if link.account_number != token:
            changed = True
            if apply:
                link.account_number = token
        if account is not None and link.bank_account_id != getattr(account, "id", None):
            changed = True
            if apply and getattr(account, "id", None):
                link.bank_account_id = int(account.id)

        if changed:
            changed_rows["bank_account_links"] += 1
            affected_models.add("BankAccountLink")

    jobs_query = (
        ImportJob.query.filter(ImportJob.error_summary.isnot(None))
        .order_by(ImportJob.id.asc())
    )
    if limit:
        jobs_query = jobs_query.limit(int(limit))
    for job in jobs_query.all():
        scanned_rows["import_jobs"] += 1
        current_summary = job.error_summary or {}
        cleaned = _sanitize_job_error_summary(current_summary)
        if cleaned == current_summary:
            continue
        changed_rows["import_jobs"] += 1
        affected_models.add("ImportJob")
        if apply:
            job.error_summary = cleaned

    if apply:
        db.session.commit()
    else:
        db.session.rollback()

    report = {
        "scanned_rows": scanned_rows,
        "changed_rows": changed_rows,
        "skipped_rows": skipped_rows,
        "affected_models": sorted(affected_models),
        "dry_run": not apply,
        "applied": bool(apply),
        "db_available": True,
        "mode": "database",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_fixture_smoke(*, output_path: Path) -> dict:
    sample_link = {
        "user_pk": 1,
        "bank_code": "0004",
        "account_number": "123-456-789012",
        "alias": "사업용",
    }
    sample_job = {
        "errors": [
            {"account": "0004-123456789012", "error": "demo"},
            {"account": "0092-998877665544", "error": "demo-2"},
        ]
    }
    safe = sanitize_account_like_value(sample_link["account_number"])
    report = {
        "scanned_rows": {"bank_account_links": 1, "import_jobs": 1},
        "changed_rows": {
            "bank_account_links": 1,
            "import_jobs": 1,
            "user_bank_accounts_created": 1,
        },
        "skipped_rows": {"bank_account_links": 0, "import_jobs": 0},
        "affected_models": ["BankAccountLink", "ImportJob", "UserBankAccount"],
        "dry_run": True,
        "applied": False,
        "db_available": False,
        "mode": "fixture",
        "sample_after": {
            "bank_account_link": {
                "bank_code": sample_link["bank_code"],
                "account_number": make_identifier_storage_token(safe.normalized_digits, prefix="acct"),
                "account_last4": safe.last4,
            },
            "import_job_error_summary": _sanitize_job_error_summary(sample_job),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remediate sensitive identifiers in bank/import rows.")
    parser.add_argument("--apply", action="store_true", help="Apply changes instead of dry-run.")
    parser.add_argument("--limit", type=int, default=None, help="Limit scanned rows per model.")
    parser.add_argument(
        "--output",
        default="reports/bank_identifier_remediation_smoke.json",
        help="Path to write the remediation report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    if not (os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")):
        if args.apply:
            raise RuntimeError("DB 환경변수가 없어서 --apply를 실행할 수 없습니다.")
        report = run_fixture_smoke(output_path=output_path)
    else:
        report = run_remediation(
            apply=bool(args.apply),
            limit=args.limit,
            output_path=output_path,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
