from __future__ import annotations

TAX_REASON_OK = "ok"
TAX_REASON_ESTIMATE_UNAVAILABLE = "estimate_unavailable"
TAX_REASON_MISSING_TAXABLE_INCOME = "missing_taxable_income"
TAX_REASON_MISSING_INCOME_CLASSIFICATION = "missing_income_classification"
TAX_REASON_MISSING_WITHHELD_TAX = "missing_withheld_tax"
TAX_REASON_MISSING_PREPAID_TAX = "missing_prepaid_tax"
TAX_REASON_PROXY_FROM_ANNUAL_INCOME = "proxy_from_annual_income"
TAX_REASON_INSUFFICIENT_PROFILE_INPUTS = "insufficient_profile_inputs"

_TAX_REASON_LEGACY_MAP = {
    "missing_official_taxable_income": TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
    "limited_proxy": TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
    "unknown": TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
}

TAX_REASON_CODES = {
    TAX_REASON_OK,
    TAX_REASON_ESTIMATE_UNAVAILABLE,
    TAX_REASON_MISSING_TAXABLE_INCOME,
    TAX_REASON_MISSING_INCOME_CLASSIFICATION,
    TAX_REASON_MISSING_WITHHELD_TAX,
    TAX_REASON_MISSING_PREPAID_TAX,
    TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
    TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
}


def normalize_tax_reason(reason: str | None, *, fallback: str = TAX_REASON_INSUFFICIENT_PROFILE_INPUTS) -> str:
    raw = str(reason or "").strip().lower()
    if not raw:
        return str(fallback)
    if raw in TAX_REASON_CODES:
        return raw
    mapped = _TAX_REASON_LEGACY_MAP.get(raw)
    if mapped:
        return str(mapped)
    return str(fallback)


NHIS_REASON_OK = "ok"
NHIS_REASON_MISSING_MEMBERSHIP_TYPE = "missing_membership_type"
NHIS_REASON_MISSING_SALARY_MONTHLY = "missing_salary_monthly"
NHIS_REASON_MISSING_NON_SALARY_INCOME = "missing_non_salary_income"
NHIS_REASON_MISSING_PROPERTY_TAX_BASE = "missing_property_tax_base"
NHIS_REASON_MISSING_SNAPSHOT = "missing_snapshot"
NHIS_REASON_DATASET_FALLBACK = "dataset_fallback"
NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE = "unknown_membership_type"
NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS = "insufficient_profile_inputs"

_NHIS_REASON_LEGACY_MAP = {
    "official_not_ready": NHIS_REASON_MISSING_SNAPSHOT,
    "input_insufficient": NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS,
    "dataset_update_error": NHIS_REASON_DATASET_FALLBACK,
    "dataset_fallback_default": NHIS_REASON_DATASET_FALLBACK,
    "dataset_stale": NHIS_REASON_DATASET_FALLBACK,
}

NHIS_REASON_CODES = {
    NHIS_REASON_OK,
    NHIS_REASON_MISSING_MEMBERSHIP_TYPE,
    NHIS_REASON_MISSING_SALARY_MONTHLY,
    NHIS_REASON_MISSING_NON_SALARY_INCOME,
    NHIS_REASON_MISSING_PROPERTY_TAX_BASE,
    NHIS_REASON_MISSING_SNAPSHOT,
    NHIS_REASON_DATASET_FALLBACK,
    NHIS_REASON_UNKNOWN_MEMBERSHIP_TYPE,
    NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS,
}


def normalize_nhis_reason(reason: str | None, *, fallback: str = NHIS_REASON_INSUFFICIENT_PROFILE_INPUTS) -> str:
    raw = str(reason or "").strip().lower()
    if not raw:
        return str(fallback)
    if raw in NHIS_REASON_CODES:
        return raw
    mapped = _NHIS_REASON_LEGACY_MAP.get(raw)
    if mapped:
        return str(mapped)
    return str(fallback)
