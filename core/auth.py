# core/auth.py
# login_required 데코레이터 (가드)

from functools import wraps
from flask import session, redirect, url_for, request


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("web_auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped
