from __future__ import annotations

from dataclasses import dataclass

from services.plan import build_runtime_plan_state, plan_label_ko

from .constants import (
    ADDON_ACCOUNT_SLOT_PRICE_KRW,
    BASIC_PRICE_KRW,
    INCLUDED_ACCOUNT_LIMITS,
    PLAN_BASIC,
    PLAN_FREE,
    PLAN_PRO,
    PRO_PRICE_KRW,
    SYNC_INTERVAL_MINUTES_BY_PLAN,
)


@dataclass(frozen=True)
class PricingPlanCard:
    code: str
    label: str
    title: str
    price_label: str
    sync_label: str
    account_limit_label: str
    tagline: str
    features: tuple[str, ...]
    cta_label_logged_out: str
    cta_label_logged_in: str
    recommended: bool = False


def format_krw(amount_krw: int) -> str:
    return f"{int(amount_krw):,}원"


def format_monthly_krw(amount_krw: int) -> str:
    return f"월 {format_krw(amount_krw)}"


def sync_interval_label(plan_code: str) -> str:
    minutes = SYNC_INTERVAL_MINUTES_BY_PLAN.get(plan_code)
    if minutes is None:
        return "자동 동기화 없음"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간마다 자동 동기화"
    return f"{minutes}분마다 자동 동기화"


def account_limit_label(plan_code: str) -> str:
    limit = int(INCLUDED_ACCOUNT_LIMITS.get(plan_code, 0))
    if limit <= 0:
        return "자동 연동 계좌 없음"
    return f"자동 연동 계좌 {limit}개 포함"


def build_pricing_plan_cards() -> tuple[PricingPlanCard, ...]:
    return (
        PricingPlanCard(
            code=PLAN_FREE,
            label=plan_label_ko(PLAN_FREE),
            title="먼저 현황을 점검해보는 시작 플랜",
            price_label="0원",
            sync_label=sync_interval_label(PLAN_FREE),
            account_limit_label=account_limit_label(PLAN_FREE),
            tagline="현재도 바로 쓸 수 있는 기본 흐름",
            features=(
                "수동 입력과 기본 요약",
                "CSV 업로드",
                "공식자료 업로드",
                "참고자료 업로드",
            ),
            cta_label_logged_out="가입하고 시작하기",
            cta_label_logged_in="현재 기능 보기",
        ),
        PricingPlanCard(
            code=PLAN_BASIC,
            label=plan_label_ko(PLAN_BASIC),
            title="혼자 일하는 프리랜서를 위한 기본 유료안",
            price_label=format_monthly_krw(BASIC_PRICE_KRW),
            sync_label=sync_interval_label(PLAN_BASIC),
            account_limit_label=account_limit_label(PLAN_BASIC),
            tagline="회수 기준 가격안",
            features=(
                "계좌 1개 자동 연동",
                "증빙 보관과 패키지 다운로드",
                "세금/정리 화면을 더 편하게 쓰는 구조",
                "현재 main에서는 아직 권한 분리 미연결",
            ),
            cta_label_logged_out="로그인하고 안내 보기",
            cta_label_logged_in="구독 안내 보기",
            recommended=True,
        ),
        PricingPlanCard(
            code=PLAN_PRO,
            label=plan_label_ko(PLAN_PRO),
            title="계좌가 여러 개이거나 더 자주 동기화가 필요한 경우",
            price_label=format_monthly_krw(PRO_PRICE_KRW),
            sync_label=sync_interval_label(PLAN_PRO),
            account_limit_label=account_limit_label(PLAN_PRO),
            tagline="회수 기준 가격안",
            features=(
                "계좌 2개 자동 연동",
                "더 짧은 자동 동기화 주기",
                "추가 계좌 확장 전제",
                "현재 main에서는 아직 권한 분리 미연결",
            ),
            cta_label_logged_out="로그인하고 안내 보기",
            cta_label_logged_in="구독 안내 보기",
        ),
    )


def build_pricing_comparison_rows() -> tuple[dict[str, str], ...]:
    return (
        {
            "label": "예상 세금/정리 기본축",
            "free": "지원",
            "basic": "지원",
            "pro": "지원",
        },
        {
            "label": "공식자료/참고자료 업로드",
            "free": "현재 main 공통 제공",
            "basic": "현재 main 공통 제공",
            "pro": "현재 main 공통 제공",
        },
        {
            "label": "자동 연동 계좌 수",
            "free": "0개",
            "basic": "1개",
            "pro": "2개",
        },
        {
            "label": "자동 동기화 주기",
            "free": "없음",
            "basic": "4시간",
            "pro": "1시간",
        },
        {
            "label": "세무사 패키지/증빙 관리",
            "free": "현재 main 공통 제공",
            "basic": "계획상 포함",
            "pro": "계획상 포함",
        },
    )


def build_current_main_capabilities() -> tuple[str, ...]:
    return (
        "로그인 사용자는 현재 동일 기능을 사용합니다.",
        "공식자료 업로드, 참고자료 업로드, 증빙 보관함, 세무사 패키지 v2가 현재 main에 연결돼 있습니다.",
        "결제 승인, 정기결제, 웹훅, 플랜별 권한 분리는 아직 연결되지 않았습니다.",
    )


def build_pricing_page_context(*, user_pk: int | None = None) -> dict[str, object]:
    runtime_state = build_runtime_plan_state(user_pk)
    return {
        "runtime_state": runtime_state,
        "plans": build_pricing_plan_cards(),
        "comparison_rows": build_pricing_comparison_rows(),
        "current_main_capabilities": build_current_main_capabilities(),
        "addon_price_label": format_monthly_krw(ADDON_ACCOUNT_SLOT_PRICE_KRW),
    }
