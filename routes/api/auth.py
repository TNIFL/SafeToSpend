from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from services.api_tokens import (
    issue_token_pair,
    revoke_all_refresh_tokens,
    revoke_token_for_user,
    rotate_refresh_token,
)
from services.auth import authenticate
from services.input_sanitize import safe_str, validate_email
from services.rate_limit import client_ip, hit_limit
from services.security_audit import audit_event

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api")


def _json_payload() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return {}


def _req_user_agent() -> str:
    return str(request.headers.get("User-Agent") or "").strip()[:255]


@api_auth_bp.post("/auth/token")
def issue_token():
    ip = client_ip()
    payload = _json_payload()
    identifier_raw = safe_str(payload.get("email") or payload.get("identifier"), max_len=254)
    identifier = validate_email(identifier_raw) if "@" in identifier_raw else identifier_raw
    password = str(payload.get("password") or "")[:256]

    limited, wait_sec = hit_limit(
        key=f"api:auth:token:ip:{ip}",
        limit=30,
        window_seconds=60,
    )
    limited_id, wait_id = hit_limit(
        key=f"api:auth:token:id:{identifier[:120].lower()}",
        limit=12,
        window_seconds=60,
    )
    if limited or limited_id:
        return jsonify({"ok": False, "message": f"요청이 많아요. {max(wait_sec, wait_id)}초 후 다시 시도해 주세요."}), 429

    ok, msg, user_pk = authenticate(identifier, password)
    if not ok or not user_pk:
        audit_event(
            "login_failed",
            user_pk=None,
            outcome="denied",
            detail="api token auth failed",
            extra={"ip": ip, "identifier": identifier[:120]},
        )
        return jsonify({"ok": False, "message": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401

    try:
        access_token, refresh_token, expires_in = issue_token_pair(
            user_pk=int(user_pk),
            user_agent=_req_user_agent(),
            ip_address=ip,
        )
    except Exception:
        audit_event(
            "login_failed",
            user_pk=int(user_pk),
            outcome="error",
            detail="api token issue failed",
            extra={"ip": ip},
        )
        return jsonify({"ok": False, "message": "로그인 처리 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."}), 503
    audit_event(
        "login_success",
        user_pk=int(user_pk),
        outcome="ok",
        detail="api token issued",
        extra={"ip": ip},
    )
    return jsonify(
        {
            "ok": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": int(expires_in),
        }
    )


@api_auth_bp.post("/auth/refresh")
def refresh_token():
    ip = client_ip()
    payload = _json_payload()
    token = safe_str(payload.get("refresh_token"), max_len=2048)
    if not token:
        return jsonify({"ok": False, "message": "리프레시 토큰이 없어요."}), 400

    limited, wait_sec = hit_limit(
        key=f"api:auth:refresh:ip:{ip}",
        limit=60,
        window_seconds=60,
    )
    if limited:
        return jsonify({"ok": False, "message": f"요청이 많아요. {wait_sec}초 후 다시 시도해 주세요."}), 429

    try:
        ok, data = rotate_refresh_token(
            refresh_token=token,
            user_agent=_req_user_agent(),
            ip_address=ip,
        )
    except Exception:
        audit_event("refresh_failed", outcome="error", detail="refresh rotate failed", extra={"ip": ip})
        return jsonify({"ok": False, "message": "토큰 갱신 중 문제가 발생했어요. 다시 로그인해 주세요."}), 503
    if not ok:
        code = str(data.get("code") or "invalid")
        if code == "reuse_detected":
            audit_event("refresh_reuse_detected", outcome="denied", detail="refresh token reuse")
        return jsonify({"ok": False, "message": str(data.get("message") or "다시 로그인해 주세요.")}), 401

    user_pk = int(data.get("user_pk") or 0)
    if user_pk > 0:
        audit_event(
            "refresh_success",
            user_pk=user_pk,
            outcome="ok",
            detail="refresh rotated",
            extra={"ip": ip},
        )
    return jsonify(
        {
            "ok": True,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "token_type": "Bearer",
            "expires_in": int(data.get("expires_in") or 0),
        }
    )


@api_auth_bp.post("/auth/logout")
def logout_api():
    user_pk = int(getattr(g, "api_user_pk", 0) or 0)
    if user_pk <= 0:
        return jsonify({"ok": False, "message": "로그인 정보가 올바르지 않아요."}), 401

    payload = _json_payload()
    refresh_token = safe_str(payload.get("refresh_token"), max_len=2048)
    ip = client_ip()

    try:
        if refresh_token:
            revoke_token_for_user(refresh_token=refresh_token, user_pk=user_pk)
        else:
            revoke_all_refresh_tokens(user_pk=user_pk)
    except Exception:
        audit_event("logout_failed", user_pk=user_pk, outcome="error", detail="api logout failed", extra={"ip": ip})
        return jsonify({"ok": False, "message": "로그아웃 처리 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."}), 503

    audit_event("logout_success", user_pk=user_pk, outcome="ok", detail="api logout", extra={"ip": ip})
    return jsonify({"ok": True, "message": "로그아웃 되었어요."})
