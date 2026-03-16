from __future__ import annotations

import re
from datetime import date, timedelta

from flask import current_app, url_for


def parse_month(value: str | None) -> date:
    if not value:
        today = date.today()
        return date(today.year, today.month, 1)
    raw = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", raw)
    if not m:
        today = date.today()
        return date(today.year, today.month, 1)
    try:
        y = int(m.group(1))
        mm = int(m.group(2))
        if y < 2000 or y > 2100 or mm < 1 or mm > 12:
            raise ValueError("out_of_range")
        return date(y, mm, 1)
    except Exception:
        today = date.today()
        return date(today.year, today.month, 1)


def month_range(first_day: date) -> tuple[date, date]:
    if first_day.month == 12:
        end = date(first_day.year + 1, 1, 1)
    else:
        end = date(first_day.year, first_day.month + 1, 1)
    return first_day, end


def calendar_grid(first_day: date) -> list[list[date]]:
    start, _ = month_range(first_day)
    grid_start = start - timedelta(days=start.weekday())
    days = [grid_start + timedelta(days=i) for i in range(42)]
    return [days[i : i + 7] for i in range(0, 42, 7)]


def safe_url(endpoint: str, **values):
    try:
        if endpoint in current_app.view_functions:
            return url_for(endpoint, **values)
    except Exception:
        return None
    return None


def cp_key(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip()
    if not key:
        return None
    return key


def evidence_defaults_from_expense_status(expense_status: str | None) -> tuple[str, str]:
    if expense_status == "business":
        return "required", "missing"
    if expense_status == "personal":
        return "not_needed", "not_needed"
    return "maybe", "missing"
