from __future__ import annotations

import os
from functools import wraps

from flask import current_app, redirect, render_template, request, session, url_for

from domain.models import User


def _configured_admin_emails() -> set[str]:
    raw = current_app.config.get("ADMIN_EMAILS")
    if raw is None:
        raw = os.getenv("ADMIN_EMAILS", "")

    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw).split(",")

    return {str(value).strip().lower() for value in values if str(value).strip()}


def current_user_is_admin() -> bool:
    try:
        user_id = int(session.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0

    if user_id <= 0:
        return False

    user = User.query.filter_by(id=user_id).first()
    if not user or not user.email:
        return False

    return user.email.strip().lower() in _configured_admin_emails()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("web_auth.login", next=request.path))

        if not current_user_is_admin():
            return (
                render_template(
                    "admin/forbidden.html",
                    message="관리자 이메일로 등록된 계정만 접근할 수 있어요.",
                ),
                403,
            )

        return view(*args, **kwargs)

    return wrapped
