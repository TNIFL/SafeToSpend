# services/dashboard_state.py
from __future__ import annotations

from core.extensions import db
from domain.models import UserDashboardState

DEFAULT_RATE = 0.15


def _to_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_rate(v, default=DEFAULT_RATE) -> float:
    try:
        r = float(v)
    except (TypeError, ValueError):
        return default
    if r > 1:
        r = r / 100.0
    return max(0.0, min(r, 0.95))


def get_state(user_pk: int) -> dict:
    row = UserDashboardState.query.get(user_pk)
    if not row:
        row = UserDashboardState(
            user_pk=user_pk,
            gross_income=0,
            expenses=0,
            rate=DEFAULT_RATE,
        )
        db.session.add(row)
        db.session.commit()

    return {
        "rev": _to_int(row.gross_income, 0),
        "exp": _to_int(row.expenses, 0),
        "rate": _to_rate(row.rate, DEFAULT_RATE),
    }


def save_state(user_pk: int, rev, exp, rate) -> None:
    row = UserDashboardState.query.get(user_pk)
    if not row:
        row = UserDashboardState(user_pk=user_pk)
        db.session.add(row)

    row.gross_income = _to_int(rev, 0)
    row.expenses = _to_int(exp, 0)
    row.rate = _to_rate(rate, DEFAULT_RATE)

    db.session.commit()
