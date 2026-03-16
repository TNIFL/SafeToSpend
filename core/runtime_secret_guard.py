from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence


DEFAULT_SECRET_KEY = "dev-secret-change-me"
DEV_APP_ENVS = {"development", "dev", "local", "test"}
LOCAL_ONLY_HOSTS = {"127.0.0.1", "localhost", "::1"}
TRUTHY = {"1", "true", "yes", "on"}


def _norm_text(value: str | None) -> str:
    return str(value or "").strip()


def _norm_lower(value: str | None) -> str:
    return _norm_text(value).lower()


def is_insecure_secret_key(secret: str | None) -> bool:
    normalized = _norm_text(secret)
    return (not normalized) or (normalized == DEFAULT_SECRET_KEY)


def resolve_runtime_bind_host(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    for key in ("FLASK_RUN_HOST", "APP_BIND_HOST", "HOST"):
        value = _norm_text(env.get(key))
        if value:
            return value
    return ""


def is_local_only_host(host: str | None) -> bool:
    normalized = _norm_lower(host)
    return normalized in LOCAL_ONLY_HOSTS


def is_probably_local_dev_process(argv: Sequence[str] | None = None) -> bool:
    parts = [str(x or "").strip().lower() for x in (argv or sys.argv or [])]
    if not parts:
        return False
    joined = " ".join(parts)
    return any(token in joined for token in ("flask", "pytest", "unittest", "app.py"))


def allow_insecure_secret_for_local_dev(
    *,
    app_env: str | None,
    bind_host: str | None,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> bool:
    env_name = _norm_lower(app_env)
    if env_name not in DEV_APP_ENVS:
        return False

    host = _norm_text(bind_host)
    if is_local_only_host(host):
        return True

    if env_name == "test":
        return True

    env = environ or os.environ
    explicit_override = _norm_lower(env.get("ALLOW_INSECURE_DEV_SECRET_KEY")) in TRUTHY
    if not host:
        return explicit_override and is_probably_local_dev_process(argv)

    return False


def validate_runtime_secret_key(
    *,
    secret: str | None,
    app_env: str | None,
    bind_host: str | None,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> None:
    if not is_insecure_secret_key(secret):
        return

    if allow_insecure_secret_for_local_dev(
        app_env=app_env,
        bind_host=bind_host,
        environ=environ,
        argv=argv,
    ):
        return

    raise RuntimeError(
        "기본 SECRET_KEY는 localhost 전용 개발 환경에서만 허용됩니다. "
        "외부 접근 가능 환경에서는 SECRET_KEY를 반드시 별도 설정하세요."
    )
