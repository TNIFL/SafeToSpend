from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta

from flask import current_app
from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer
from sqlalchemy import and_

from core.extensions import db
from core.runtime_secret_guard import validate_runtime_secret_key
from core.time import utcnow
from domain.models import RefreshToken

ACCESS_TOKEN_SALT = "sts-access-token-v1"
ACCESS_TOKEN_EXPIRES_SECONDS = 60 * 15
REFRESH_TOKEN_EXPIRES_SECONDS = 60 * 60 * 24 * 30


def _serializer() -> URLSafeTimedSerializer:
    secret = str(current_app.config.get("SECRET_KEY") or "").strip()
    validate_runtime_secret_key(
        secret=secret,
        app_env=str(current_app.config.get("APP_ENV") or ""),
        bind_host=str(current_app.config.get("RUNTIME_BIND_HOST") or ""),
        environ=os.environ,
    )
    return URLSafeTimedSerializer(secret_key=secret, salt=ACCESS_TOKEN_SALT)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_access_token(*, user_pk: int) -> str:
    payload = {
        "typ": "access",
        "sub": int(user_pk),
        "iat": int(utcnow().timestamp()),
    }
    return _serializer().dumps(payload)


def verify_access_token(token: str) -> tuple[bool, int | None, str]:
    raw = str(token or "").strip()
    if not raw:
        return False, None, "토큰이 없어요."
    try:
        payload = _serializer().loads(raw, max_age=ACCESS_TOKEN_EXPIRES_SECONDS)
    except (BadSignature, BadTimeSignature):
        return False, None, "토큰이 유효하지 않거나 만료됐어요."
    except Exception:
        return False, None, "토큰 확인 중 문제가 발생했어요."
    if not isinstance(payload, dict):
        return False, None, "토큰 형식이 올바르지 않아요."
    if str(payload.get("typ") or "") != "access":
        return False, None, "토큰 유형이 올바르지 않아요."
    try:
        uid = int(payload.get("sub") or 0)
    except Exception:
        uid = 0
    if uid <= 0:
        return False, None, "토큰 사용자 정보가 올바르지 않아요."
    return True, uid, "ok"


def issue_token_pair(
    *,
    user_pk: int,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str, int]:
    now = utcnow()
    refresh_plain = secrets.token_urlsafe(48)
    refresh_hash = _sha256(refresh_plain)
    row = RefreshToken(
        user_pk=int(user_pk),
        token_hash=refresh_hash,
        created_at=now,
        expires_at=now + timedelta(seconds=REFRESH_TOKEN_EXPIRES_SECONDS),
        revoked_at=None,
        replaced_by_id=None,
        user_agent=(str(user_agent or "").strip()[:255] or None),
        ip_address=(str(ip_address or "").strip()[:64] or None),
    )
    db.session.add(row)
    db.session.commit()
    access_token = build_access_token(user_pk=int(user_pk))
    return access_token, refresh_plain, ACCESS_TOKEN_EXPIRES_SECONDS


def revoke_all_refresh_tokens(*, user_pk: int) -> int:
    now = utcnow()
    q = (
        RefreshToken.query.filter(RefreshToken.user_pk == int(user_pk))
        .filter(RefreshToken.revoked_at.is_(None))
    )
    count = 0
    for row in q.all():
        row.revoked_at = now
        count += 1
    if count:
        db.session.commit()
    return count


def rotate_refresh_token(
    *,
    refresh_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[bool, dict]:
    raw = str(refresh_token or "").strip()
    if not raw:
        return False, {"message": "리프레시 토큰이 없어요.", "code": "missing_token"}

    token_hash = _sha256(raw)
    row = RefreshToken.query.filter_by(token_hash=token_hash).first()
    if not row:
        return False, {"message": "다시 로그인해 주세요.", "code": "invalid_token"}

    now = utcnow()
    if row.revoked_at is not None:
        revoke_all_refresh_tokens(user_pk=int(row.user_pk))
        return False, {"message": "보안을 위해 다시 로그인해 주세요.", "code": "reuse_detected"}

    if row.expires_at and row.expires_at < now:
        row.revoked_at = now
        db.session.add(row)
        db.session.commit()
        return False, {"message": "로그인이 만료됐어요. 다시 로그인해 주세요.", "code": "expired"}

    new_plain = secrets.token_urlsafe(48)
    new_hash = _sha256(new_plain)
    new_row = RefreshToken(
        user_pk=int(row.user_pk),
        token_hash=new_hash,
        created_at=now,
        expires_at=now + timedelta(seconds=REFRESH_TOKEN_EXPIRES_SECONDS),
        revoked_at=None,
        replaced_by_id=None,
        user_agent=(str(user_agent or "").strip()[:255] or None),
        ip_address=(str(ip_address or "").strip()[:64] or None),
    )
    db.session.add(new_row)
    db.session.flush()

    row.revoked_at = now
    row.replaced_by_id = int(new_row.id)
    db.session.add(row)
    db.session.commit()

    access_token = build_access_token(user_pk=int(row.user_pk))
    return True, {
        "access_token": access_token,
        "refresh_token": new_plain,
        "expires_in": ACCESS_TOKEN_EXPIRES_SECONDS,
        "user_pk": int(row.user_pk),
    }


def revoke_token_by_hash(*, refresh_token: str) -> bool:
    raw = str(refresh_token or "").strip()
    if not raw:
        return False
    token_hash = _sha256(raw)
    row = RefreshToken.query.filter(
        and_(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
    ).first()
    if not row:
        return False
    row.revoked_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return True


def revoke_token_for_user(*, refresh_token: str, user_pk: int) -> bool:
    raw = str(refresh_token or "").strip()
    uid = int(user_pk or 0)
    if not raw or uid <= 0:
        return False
    token_hash = _sha256(raw)
    row = (
        RefreshToken.query.filter(
            and_(
                RefreshToken.token_hash == token_hash,
                RefreshToken.user_pk == uid,
                RefreshToken.revoked_at.is_(None),
            )
        ).first()
    )
    if not row:
        return False
    row.revoked_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return True
