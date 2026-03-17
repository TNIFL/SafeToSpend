from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
from flask import Flask

from core.extensions import db
from services.billing.toss_client import (
    TossBillingConfigError,
    build_billing_key_cipher_for_version,
    get_active_billing_key_version,
)


REQUIRED_BILLING_TABLES = (
    "billing_customers",
    "billing_methods",
    "billing_method_registration_attempts",
    "billing_checkout_intents",
    "billing_subscriptions",
    "billing_subscription_items",
    "billing_payment_attempts",
    "billing_payment_events",
    "entitlement_change_logs",
)
REQUIRED_USER_COLUMNS = ("plan_code", "plan_status", "extra_account_slots")
REQUIRED_ENV_KEYS = (
    "TOSS_PAYMENTS_CLIENT_KEY",
    "TOSS_PAYMENTS_SECRET_KEY",
)


@dataclass(frozen=True)
class BillingStartupCheckReport:
    mode: str
    ok: bool
    errors: tuple[str, ...]


class BillingStartupCheckError(RuntimeError):
    pass


def _norm_mode(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if v in {"off", "disabled", "none"}:
        return "off"
    if v in {"strict", "hard"}:
        return "strict"
    if v in {"warn", "warning", "soft"}:
        return "warn"
    return "warn"


def resolve_billing_guard_mode(app_env: str | None, override: str | None = None) -> str:
    forced = _norm_mode(override)
    if str(override or "").strip():
        return forced
    env = str(app_env or "").strip().lower()
    if env in {"production", "prod", "staging", "stage"}:
        return "strict"
    return "warn"


def _read_env(name: str, app: Flask) -> str:
    direct = str(app.config.get(name) or "").strip()
    if direct:
        return direct
    return str(os.getenv(name) or "").strip()


def _validate_required_env(app: Flask) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_ENV_KEYS:
        if not _read_env(key, app):
            errors.append(f"필수 환경변수 누락: {key}")
    try:
        version = get_active_billing_key_version()
        build_billing_key_cipher_for_version(version)
    except TossBillingConfigError as e:
        errors.append(f"BILLING_KEY_ENCRYPTION_SECRET 검증 실패: {str(e)}")
    except Exception as e:
        errors.append(f"billing 암호화 초기화 실패: {type(e).__name__}")
    return errors


def _missing(items: Iterable[str], present: set[str]) -> list[str]:
    return [x for x in items if x not in present]


def _validate_required_schema() -> list[str]:
    errors: list[str] = []
    inspector = sa.inspect(db.engine)
    table_names = set(inspector.get_table_names())
    missing_tables = _missing(REQUIRED_BILLING_TABLES, table_names)
    if missing_tables:
        errors.append(f"billing 필수 테이블 누락: {', '.join(missing_tables)}")
    if "users" not in table_names:
        errors.append("users 테이블이 없습니다.")
        return errors
    try:
        user_columns = {str(col.get("name") or "").strip() for col in inspector.get_columns("users")}
    except Exception as e:
        errors.append(f"users 컬럼 조회 실패: {type(e).__name__}")
        return errors
    missing_cols = _missing(REQUIRED_USER_COLUMNS, user_columns)
    if missing_cols:
        errors.append(f"users 필수 컬럼 누락: {', '.join(missing_cols)}")
    return errors


def run_billing_startup_checks(app: Flask) -> BillingStartupCheckReport:
    mode = resolve_billing_guard_mode(
        str(app.config.get("APP_ENV") or app.config.get("FLASK_ENV") or "").strip().lower(),
        override=str(app.config.get("BILLING_GUARD_MODE") or "").strip(),
    )
    if mode == "off":
        return BillingStartupCheckReport(mode=mode, ok=True, errors=tuple())

    errors: list[str] = []
    errors.extend(_validate_required_env(app))
    try:
        errors.extend(_validate_required_schema())
    except Exception as e:
        errors.append(f"schema 점검 실패: {type(e).__name__}")

    ok = not errors
    if ok:
        return BillingStartupCheckReport(mode=mode, ok=True, errors=tuple())

    prefix = "[BILLING_STARTUP_CHECK]"
    msg = "; ".join(errors)
    if mode == "strict":
        raise BillingStartupCheckError(f"{prefix} {msg}")
    app.logger.error("%s %s", prefix, msg)
    return BillingStartupCheckReport(mode=mode, ok=False, errors=tuple(errors))
