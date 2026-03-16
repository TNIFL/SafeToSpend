from __future__ import annotations

import re
from datetime import date


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)
_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")


def safe_str(value: object, *, max_len: int = 255, allow_newline: bool = False) -> str:
    text = str(value or "")
    text = _CONTROL_CHARS_RE.sub("", text)
    if not allow_newline:
        text = text.replace("\r", " ").replace("\n", " ")
    text = text.strip()
    if max_len > 0:
        text = text[:max_len]
    return text


def parse_int_krw(value: object, *, allow_negative: bool = False) -> int | None:
    if value is None:
        return None
    text = safe_str(value, max_len=64, allow_newline=False)
    if not text:
        return None

    sign = -1 if text.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None

    try:
        out = int(digits) * sign
    except Exception:
        return None
    if out < 0 and not allow_negative:
        return None
    return out


def parse_bool_yn(value: object) -> bool | None:
    raw = safe_str(value, max_len=16).lower()
    if raw in {"1", "true", "yes", "y", "on", "checked"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return None


def parse_date_ym(value: object) -> str | None:
    raw = safe_str(value, max_len=7)
    if not raw:
        return None
    m = _YM_RE.fullmatch(raw)
    if not m:
        return None
    yy = int(m.group(1))
    mm = int(m.group(2))
    if yy < 1900 or yy > 2100:
        return None
    if mm < 1 or mm > 12:
        return None
    return f"{yy:04d}-{mm:02d}"


def validate_year_range(value: object, *, min_year: int = 2000, max_year: int = 2100) -> int | None:
    raw = safe_str(value, max_len=8)
    if not raw or not raw.isdigit():
        return None
    year = int(raw)
    if year < int(min_year) or year > int(max_year):
        return None
    return year


def clamp_int(value: object, *, minimum: int = 0, maximum: int = 2_147_483_647, default: int = 0) -> int:
    try:
        n = int(value)
    except Exception:
        n = int(default)
    if n < minimum:
        return int(minimum)
    if n > maximum:
        return int(maximum)
    return n


def validate_email(value: object, *, max_len: int = 254) -> str | None:
    email = safe_str(value, max_len=max_len).lower()
    if not email:
        return None
    if len(email) < 5:
        return None
    if not _EMAIL_RE.fullmatch(email):
        return None
    return email


def today_ym() -> str:
    now = date.today()
    return f"{now.year:04d}-{now.month:02d}"
