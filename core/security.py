from __future__ import annotations

import secrets
from urllib.parse import urlparse

from flask import Request, request, session


_INVALID_PATH_VALUES = {"", "none", "/none", "null", "/null", "undefined", "/undefined"}


def sanitize_next_url(raw: str | None, fallback: str) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() in _INVALID_PATH_VALUES:
        return fallback
    try:
        parsed = urlparse(text)
    except Exception:
        return fallback
    if parsed.scheme or parsed.netloc:
        return fallback
    path = str(parsed.path or "").strip()
    if not path.startswith("/"):
        return fallback
    if path.lower() in _INVALID_PATH_VALUES:
        return fallback
    if path.startswith("//"):
        return fallback
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def safe_referrer_or_fallback(*, req: Request, fallback: str) -> str:
    ref = str(req.referrer or "").strip()
    if not ref:
        return fallback
    try:
        parsed = urlparse(ref)
    except Exception:
        return fallback
    if parsed.netloc and parsed.netloc != req.host:
        return fallback
    path = str(parsed.path or "").strip()
    if not path.startswith("/"):
        return fallback
    if path.startswith("//"):
        return fallback
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def wants_json_response(*, req: Request | None = None) -> bool:
    req = req or request
    if (req.args.get("format") or "").strip().lower() == "json":
        return True
    if req.path.startswith("/api/"):
        return True
    accept = (req.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    ctype = (req.headers.get("Content-Type") or "").lower()
    if "application/json" in ctype:
        return True
    requested_with = (req.headers.get("X-Requested-With") or "").lower()
    if requested_with == "xmlhttprequest":
        return True
    return False


def get_or_create_csrf_token() -> str:
    token = str(session.get("_csrf_token") or "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        session["_csrf_token"] = token
        session.modified = True
    return token


def is_valid_csrf_token(value: str | None) -> bool:
    expected = str(session.get("_csrf_token") or "").strip()
    actual = str(value or "").strip()
    return bool(expected and actual and secrets.compare_digest(expected, actual))

