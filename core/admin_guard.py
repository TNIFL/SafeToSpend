from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from flask import jsonify, render_template, request, session, url_for, redirect

from core.security import wants_json_response
from domain.models import User

F = TypeVar("F", bound=Callable[..., Any])


def current_user() -> User | None:
    try:
        uid = int(session.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return None
    try:
        return User.query.filter_by(id=uid).first()
    except Exception:
        session.pop("user_id", None)
        return None


def is_admin_user(user: User | None) -> bool:
    if not user:
        return False
    return bool(getattr(user, "is_admin", False))


def _deny_admin(message: str):
    msg = str(message or "관리자만 접근할 수 있어요.")
    if request.path.startswith("/admin/api/") or wants_json_response(req=request):
        return jsonify({"ok": False, "message": msg}), 403
    try:
        return render_template("admin/forbidden.html", message=msg), 403
    except Exception:
        return msg, 403


def admin_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("web_auth.login", next=request.path))

        if not is_admin_user(user):
            return _deny_admin("관리자만 접근할 수 있어요.")

        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]
