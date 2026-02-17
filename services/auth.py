# services/auth.py
from __future__ import annotations

from core.extensions import db
from domain.models import User, Settings


def register_user(email: str, password: str) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False, "올바른 이메일을 입력해 주세요."
    if not password or len(password) < 8:
        return False, "비밀번호는 8자 이상으로 설정해 주세요."

    if User.query.filter_by(email=email).first():
        return False, "이미 가입된 이메일입니다."

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()  # user.id 확보

    # settings는 0a656...에서 safe_to_spend_settings 대신 새로 생김
    st = Settings(user_pk=user.id, default_tax_rate=0.15, custom_rates={})
    db.session.add(st)

    db.session.commit()
    return True, "가입 완료"


def authenticate(identifier: str, password: str) -> tuple[bool, str, int | None]:
    # 이제 닉네임/생년월일 컬럼이 DB에 없음 → 이메일만 받는 게 안전
    email = (identifier or "").strip().lower()

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password or ""):
        return False, "이메일 또는 비밀번호가 올바르지 않습니다.", None

    return True, "ok", user.id
