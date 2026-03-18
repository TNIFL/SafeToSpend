from __future__ import annotations

PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PRO = "pro"
PLAN_VALUES = (PLAN_FREE, PLAN_BASIC, PLAN_PRO)

BASIC_PRICE_KRW = 6900
PRO_PRICE_KRW = 12900
ADDON_ACCOUNT_SLOT_PRICE_KRW = 3000

INCLUDED_ACCOUNT_LIMITS: dict[str, int] = {
    PLAN_FREE: 0,
    PLAN_BASIC: 1,
    PLAN_PRO: 2,
}

SYNC_INTERVAL_MINUTES_BY_PLAN: dict[str, int | None] = {
    PLAN_FREE: None,
    PLAN_BASIC: 240,
    PLAN_PRO: 60,
}

BILLING_RUNTIME_MODE = "display_only"
