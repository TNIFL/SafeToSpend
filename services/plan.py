from __future__ import annotations

from dataclasses import dataclass

from services.billing.constants import PLAN_BASIC, PLAN_FREE, PLAN_PRO, PLAN_VALUES


@dataclass(frozen=True)
class RuntimePlanState:
    current_plan_code: str
    current_plan_label: str
    subscription_ready: bool
    runtime_mode: str
    status_label: str
    note: str


def normalize_plan(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in PLAN_VALUES:
        return raw
    return PLAN_FREE


def plan_label_ko(plan_code: str | None) -> str:
    code = normalize_plan(plan_code)
    if code == PLAN_BASIC:
        return "베이직"
    if code == PLAN_PRO:
        return "프로"
    return "무료"


def build_runtime_plan_state(user_pk: int | None = None) -> RuntimePlanState:
    del user_pk
    return RuntimePlanState(
        current_plan_code=PLAN_FREE,
        current_plan_label=plan_label_ko(PLAN_FREE),
        subscription_ready=False,
        runtime_mode="display_only",
        status_label="구독 준비 중",
        note=(
            "현재 main에서는 결제 승인, 정기결제, 웹훅, 플랜별 권한 분리가 아직 연결되지 않았습니다. "
            "로그인 사용자는 현재 동일 기능을 사용하고, 이 페이지는 회수한 플랜 안내만 제공합니다."
        ),
    )
