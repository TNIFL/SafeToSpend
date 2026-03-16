# core/time.py
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    """프로젝트 표준 '현재 시각' (KST 기준 naive datetime).

    이 코드베이스는 대부분 DateTime 컬럼을 timezone 없는(naive) 형태로 쓰고,
    화면/월경계/사용자 체감은 KST를 기준으로 설계되어 있음.

    ✅ 따라서 DB에는 'KST naive'로 저장/표시하는 것을 기본으로 한다.
    """
    return datetime.now(timezone.utc).astimezone(KST).replace(tzinfo=None)


def kstnow() -> datetime:
    """명시적으로 KST naive now."""
    return utcnow()


def to_kst(dt: datetime | None) -> datetime | None:
    """dt가 naive면 KST로 간주해 tzinfo만 붙여 반환(필터/표시용)."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)