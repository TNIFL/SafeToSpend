from __future__ import annotations

from typing import Any

from core.extensions import db
from services.nhis_profile import get_or_create_nhis_profile, nhis_profile_to_dict


def load_canonical_nhis_profile(
    *,
    user_pk: int,
    month_key: str | None = None,
    prefer_assets: bool = True,
) -> dict[str, Any]:
    """건보 추정 공통 입력 프로필을 단일 경로로 반환한다.

    우선순위
    1) (기본) 자산 입력을 NhisUserProfile로 동기화 시도
    2) NhisUserProfile을 읽어 최종 추정 입력으로 사용
    """
    if prefer_assets:
        try:
            # local import: 순환 의존 최소화
            from services.assets_profile import sync_assets_to_nhis_profile

            sync_assets_to_nhis_profile(user_pk=user_pk, month_key=month_key)
        except Exception:
            db.session.rollback()

    row = get_or_create_nhis_profile(user_pk)
    profile = nhis_profile_to_dict(row)
    if month_key:
        profile["target_month"] = str(month_key)
    return profile

