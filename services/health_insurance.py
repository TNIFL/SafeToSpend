# services/health_insurance.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_

from core.extensions import db
from domain.models import Transaction, SafeToSpendSettings
from services.official_refs.guard import check_nhis_ready


# “귀찮지 않게”를 목표로, 실제 거래내역에서 가장 흔한 표기 위주로만 잡는다.
_NHI_KEYWORDS = [
    "국민건강보험",
    "건강보험공단",
    "건강보험",
    "NHIS",
    "장기요양",
]


def _month_range_naive(month_key: str) -> tuple[datetime, datetime]:
    y, m = month_key.split("-")
    y = int(y)
    m = int(m)
    start = datetime(y, m, 1, 0, 0, 0)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0)
    return start, end


def _get_settings(user_pk: int) -> SafeToSpendSettings:
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()
    return s


def _ilike_any(col, keywords: list[str]):
    conds = []
    for k in keywords:
        k = (k or "").strip()
        if not k:
            continue
        conds.append(col.ilike(f"%{k}%"))
    if not conds:
        return col.ilike("__never__")
    return or_(*conds)


@dataclass(frozen=True)
class NhiInfo:
    month_key: str
    monthly_krw: int
    due_krw: int
    paid_this_month: bool
    source: str  # manual/detected/unknown

    matched_tx_id: int | None = None
    matched_at: datetime | None = None
    matched_amount_krw: int | None = None


def get_monthly_health_insurance_buffer(profile: dict | None) -> tuple[int, str | None]:
    p = profile if isinstance(profile, dict) else {}
    nhis_type = str(p.get("health_insurance_type") or "unknown").strip()
    if nhis_type not in {"employed", "regional", "dependent", "unknown"}:
        nhis_type = "unknown"

    monthly_raw = p.get("health_insurance_monthly_krw")
    if monthly_raw is None:
        monthly_raw = p.get("monthly_nhis_amount")
    monthly = 0
    if monthly_raw is not None:
        try:
            monthly = int(monthly_raw)
        except Exception:
            monthly = 0
    if monthly < 0:
        monthly = 0

    # 정책: 직장/피부양자는 이번 달 별도 보관액을 0으로 두고 안내만 노출한다.
    if nhis_type in {"employed", "dependent"}:
        return 0, "직장/피부양자는 별도 납부가 아닐 수 있어요."

    if monthly > 0:
        return int(monthly), None
    return 0, "건보료(월 납부액)를 입력하면 더 정확해져요."


def infer_nhi_for_month(user_pk: int, month_key: str) -> NhiInfo:
    """
    목표: Safe-to-Spend UX에서 '이번 달 건보료(예정)'을 귀찮지 않게 제공.

    우선순위
    1) 사용자 설정(nhi_monthly_krw)이 있으면 그 값 사용
    2) 없으면 최근 4개월 내 거래내역에서 키워드 출금을 찾아서 월 금액 추정
    3) 이번 달에 이미 납부 내역이 있으면 due=0 (중복 차감 방지)
    """

    s = _get_settings(user_pk)

    monthly_krw = 0
    source = "unknown"
    try:
        from services.nhis_estimator import estimate_nhis_monthly_dict
        from services.nhis_unified import load_canonical_nhis_profile
        from services.nhis_rates import ensure_active_snapshot

        ready = check_nhis_ready()
        if bool(ready.get("ready")):
            status = ensure_active_snapshot(refresh_if_stale_days=30, refresh_timeout=6)
            nhis_profile = load_canonical_nhis_profile(
                user_pk=user_pk,
                month_key=month_key,
                prefer_assets=True,
            )
            estimate = estimate_nhis_monthly_dict(nhis_profile, status.snapshot)
            est_total = int(estimate.get("total_est_krw") or 0)
            if bool(estimate.get("can_estimate")) and est_total > 0:
                monthly_krw = est_total
                source = "nhis_estimate"
    except Exception:
        # 추정 서비스 실패 시 기존 로직으로 폴백
        monthly_krw = 0
        source = "unknown"

    manual = int(getattr(s, "nhi_monthly_krw", 0) or 0)
    if monthly_krw <= 0 and manual > 0:
        monthly_krw = manual
        source = "manual"

    # 기존 사용자 데이터 호환:
    # 프로필(세금 설정)에만 건보료 월 납부액이 있고 Settings에는 비어있는 경우를 커버한다.
    if monthly_krw <= 0:
        try:
            from services.onboarding import get_tax_profile  # local import to avoid import cycle

            profile = get_tax_profile(user_pk)
            raw_profile_monthly = profile.get("health_insurance_monthly_krw")
            profile_monthly = int(raw_profile_monthly or 0)
            if profile_monthly > 0:
                monthly_krw = int(profile_monthly)
                source = "profile"
        except Exception:
            pass

    # NHIS 통합 입력 경로 호환:
    # 공식 스냅샷 게이트가 닫혀도 사용자가 직접 입력한 최근 고지 금액은 캘린더에 보여준다.
    if monthly_krw <= 0:
        try:
            from services.nhis_profile import (
                get_or_create_nhis_profile,
                list_nhis_bill_history,
                nhis_profile_to_dict,
            )

            nhis_profile = nhis_profile_to_dict(get_or_create_nhis_profile(user_pk))
            last_bill_total = int(nhis_profile.get("last_bill_total_krw") or 0)
            last_bill_health = int(nhis_profile.get("last_bill_health_only_krw") or 0)
            if last_bill_total > 0:
                monthly_krw = int(last_bill_total)
                source = "nhis_profile_bill_total"
            elif last_bill_health > 0:
                monthly_krw = int(last_bill_health)
                source = "nhis_profile_bill_health"
            else:
                for row in list_nhis_bill_history(user_pk):
                    candidate = int((row or {}).get("total_krw") or (row or {}).get("health_only_krw") or 0)
                    if candidate <= 0:
                        continue
                    monthly_krw = int(candidate)
                    source = "nhis_bill_history"
                    break
        except Exception:
            pass

    # 최근 4개월 범위(넉넉하게)에서 후보 탐색
    y, m = [int(x) for x in month_key.split("-")]
    m0 = m - 4
    y0 = y
    while m0 <= 0:
        y0 -= 1
        m0 += 12

    start_4m = datetime(y0, m0, 1, 0, 0, 0)
    end_cur = _month_range_naive(month_key)[1]

    matches = (
        db.session.query(Transaction)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_4m, Transaction.occurred_at < end_cur)
        .filter(
            or_(
                _ilike_any(Transaction.counterparty, _NHI_KEYWORDS),
                _ilike_any(Transaction.memo, _NHI_KEYWORDS),
            )
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(80)
        .all()
    )

    # monthly 추정: 설정이 없으면 반복금액(빈도) 우선
    if monthly_krw <= 0 and matches:
        freq: dict[int, int] = {}
        for t in matches:
            amt = int(t.amount_krw or 0)
            if amt <= 0:
                continue
            freq[amt] = freq.get(amt, 0) + 1
        if freq:
            monthly_krw = sorted(freq.items(), key=lambda x: (-x[1], -x[0]))[0][0]
            source = "detected"

    # 이번 달 납부 여부 체크(중복 차감 방지)
    start_m, end_m = _month_range_naive(month_key)
    paid_this_month = False
    matched = None

    if matches:
        for t in matches:
            if start_m <= t.occurred_at < end_m:
                if monthly_krw > 0 and abs(int(t.amount_krw) - int(monthly_krw)) > 2000:
                    continue
                matched = t
                paid_this_month = True
                break

    due_krw = 0
    if monthly_krw > 0:
        due_krw = 0 if paid_this_month else monthly_krw

    return NhiInfo(
        month_key=month_key,
        monthly_krw=int(monthly_krw or 0),
        due_krw=int(due_krw or 0),
        paid_this_month=bool(paid_this_month),
        source=source,
        matched_tx_id=(matched.id if matched else None),
        matched_at=(matched.occurred_at if matched else None),
        matched_amount_krw=(int(matched.amount_krw) if matched else None),
    )
