# services/risk.py
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func

from core.extensions import db
from core.time import utcnow
from domain.models import (
    Transaction,
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    TaxBufferLedger,
    SafeToSpendSettings,
    RecurringCandidate,
)
from services.onboarding import (
    TAX_ANNUAL_EXPENSE_KEYS,
    TAX_ANNUAL_GROSS_INCOME_KEYS,
    TAX_INDUSTRY_GROUPS,
    TAX_PREPAID_TAX_ANNUAL_KEYS,
    TAX_PREV_INCOME_BANDS,
    TAX_WITHHOLDING_33,
    TAX_WITHHELD_TAX_ANNUAL_KEYS,
    evaluate_tax_required_inputs,
    get_tax_profile,
    is_tax_profile_complete,
)
from services.income_hybrid import aggregate_income_override, pick_income_override_for_month
from services.nhis_runtime import build_nhis_recovery_cta, compute_nhis_monthly_buffer
from services.official_data_effects import (
    collect_official_data_effects_for_user,
    summarize_official_data_effects,
)
from services.receipt_tax_effects import compute_receipt_tax_effects_for_month
from services.tax_package import build_tax_package_preview
from services.accuracy_reason_codes import (
    TAX_REASON_ESTIMATE_UNAVAILABLE,
    TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
    TAX_REASON_MISSING_INCOME_CLASSIFICATION,
    TAX_REASON_MISSING_PREPAID_TAX,
    TAX_REASON_MISSING_TAXABLE_INCOME,
    TAX_REASON_MISSING_WITHHELD_TAX,
    TAX_REASON_OK,
    TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
    normalize_tax_reason,
)
from services.reference.tax_reference import (
    calculate_local_income_tax,
    calculate_national_income_tax,
    get_tax_reference_snapshot,
)
from services.tax_official_core import compute_tax_official_core

KST = ZoneInfo("Asia/Seoul")


def normalize_counterparty_key(counterparty: str | None) -> str:
    """거래처 키를 규칙/후보 계산에서 공통으로 쓰는 형태로 정규화."""
    return " ".join(str(counterparty or "").split()).strip().lower()


def _parse_preview_amount(raw: str | None) -> int:
    s = str(raw or "").replace(",", "").replace("원", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def _pick_first(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def compute_landing_sample_preview(csv_path: str) -> dict[str, int | str]:
    """랜딩 샘플 미리보기용 요약 계산 (DB 미사용, 고정 샘플 파일 전용)."""
    month_key = datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m")
    month_counter: dict[str, int] = {}
    income_sum = 0
    business_expense = 0
    required_missing = 0
    review_needed = 0
    withholding_base = 0
    row_count = 0

    business_keywords = (
        "업무",
        "소프트웨어",
        "장비",
        "서버",
        "cloud",
        "외근",
        "교통",
        "택시",
        "구독",
    )
    personal_keywords = ("마트", "장보기", "편의점", "개인")
    ambiguous_keywords = ("카드결제", "misc", "기타")

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_count += 1
                occurred_at = _pick_first(row, ("거래일시", "date", "occurred_at"))
                if occurred_at and len(occurred_at) >= 7:
                    mk = occurred_at[:7]
                    month_counter[mk] = month_counter.get(mk, 0) + 1

                in_amt = _parse_preview_amount(_pick_first(row, ("입금", "in_amount", "credit")))
                out_amt = _parse_preview_amount(_pick_first(row, ("출금", "out_amount", "debit")))
                amount = 0
                direction = ""

                if in_amt > 0 and out_amt <= 0:
                    direction = "in"
                    amount = in_amt
                elif out_amt > 0 and in_amt <= 0:
                    direction = "out"
                    amount = out_amt
                elif in_amt > 0 and out_amt > 0:
                    if in_amt >= out_amt:
                        direction = "in"
                        amount = in_amt
                    else:
                        direction = "out"
                        amount = out_amt
                else:
                    amount_col = _parse_preview_amount(_pick_first(row, ("금액", "amount")))
                    if amount_col != 0:
                        if amount_col < 0:
                            direction = "out"
                            amount = abs(amount_col)
                        else:
                            direction = "in"
                            amount = amount_col

                if amount <= 0:
                    continue

                counterparty = _pick_first(row, ("거래처", "counterparty", "merchant"))
                memo = _pick_first(row, ("적요", "memo", "description"))
                text = f"{counterparty} {memo}".lower()

                if direction == "in":
                    income_sum += amount
                    if ("3.3" in text) or ("원천" in text):
                        withholding_base += amount
                    continue

                if any(k in text for k in business_keywords):
                    business_expense += amount
                    required_missing += 1
                elif any(k in text for k in personal_keywords):
                    pass
                elif any(k in text for k in ambiguous_keywords):
                    review_needed += 1
                else:
                    review_needed += 1
    except Exception:
        # 샘플 파일 이상 시에도 랜딩은 항상 열려야 함
        return {
            "month_key": month_key,
            "total_setaside_recommended": 0,
            "tax_setaside_recommended": 0,
            "health_insurance_buffer": 0,
            "required_missing_count": 0,
            "review_needed_count": 0,
            "sample_rows": 0,
        }

    if month_counter:
        month_key = max(month_counter.items(), key=lambda x: x[1])[0]

    tax_rate = 0.15
    est_profit = max(0, int(income_sum) - int(business_expense))
    base_tax = int(est_profit * tax_rate)
    ref_year = int(month_key[:4]) if len(month_key) >= 4 and str(month_key[:4]).isdigit() else datetime.now().year
    local_tax = int(calculate_local_income_tax(national_income_tax_krw=base_tax, target_year=ref_year))
    tax_before_withheld = max(0, base_tax + local_tax)
    withheld_est = int(round(int(withholding_base) * 0.033))
    withheld_est = min(withheld_est, tax_before_withheld)
    tax_due_est = max(0, tax_before_withheld - withheld_est)

    # 샘플 화면은 기본값 가정(지역 건보료 10만원)
    health_insurance_buffer = 100_000
    total_setaside = int(tax_due_est) + int(health_insurance_buffer)

    return {
        "month_key": month_key,
        "total_setaside_recommended": int(total_setaside),
        "tax_setaside_recommended": int(tax_due_est),
        "health_insurance_buffer": int(health_insurance_buffer),
        "required_missing_count": int(required_missing),
        "review_needed_count": int(review_needed),
        "sample_rows": int(row_count),
    }


def _month_key_now() -> str:
    """현재 시각 기준(한국시간) YYYY-MM"""
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    return now_kst.strftime("%Y-%m")


def _month_range_kst_naive(month_key: str) -> tuple[datetime, datetime]:
    """month_key(YYYY-MM)의 '한국시간 월 경계'를 **naive datetime** 범위로 반환.

    이 프로젝트는 Transaction.occurred_at을 timezone 없는 datetime으로 쓰는 전제가 강합니다.
    (캘린더/보관함/패키지 등 대부분이 naive datetime을 KST로 해석)

    ✅ 따라서 리스크/요약 집계도 월 경계를 KST naive로 맞춥니다.
    """

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
    """settings 테이블(= SafeToSpendSettings 매핑)에 사용자 기본 세율이 없으면 생성."""
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()
    return s


def _as_kst_date_str(dt: datetime) -> str:
    """Transaction.occurred_at를 KST 기준 날짜 문자열로 표시.

    이 프로젝트의 DateTime 컬럼은 tz 없는 값(naive)이 많고,
    화면/월경계/사용자 체감은 KST 기준으로 설계되어 있습니다.

    ✅ 따라서 tz 없는 값은 'KST로 간주'하여 표시합니다.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).strftime("%Y-%m-%d")


def _tax_reference_year(month_key: str | None = None) -> int:
    key = str(month_key or "").strip()
    if len(key) >= 4 and key[:4].isdigit():
        return int(key[:4])
    return int(utcnow().strftime("%Y"))


def _progressive_national_income_tax(annual_taxable_krw: int, *, target_year: int | None = None) -> int:
    """
    종합소득세 추정용 누진 계산(단순화).
    - annual_taxable_krw: 연간 과세표준 추정(원)
    - 반환: 연간 국세 추정(원)
    """
    year = int(target_year or _tax_reference_year())
    return int(calculate_national_income_tax(taxable_income_krw=annual_taxable_krw, target_year=year))


@dataclass(frozen=True)
class RiskSummary:
    """주요 리스크를 한 번에 묶어서 쓰기 위한 요약 구조."""
    month_key: str

    gross_income_krw: int
    expenses_krw: int

    evidence_missing_required: int
    evidence_missing_maybe: int
    expense_needs_review: int
    income_unknown: int

    buffer_total_krw: int
    buffer_target_krw: int
    buffer_shortage_krw: int
    
    
# ✅ RiskSummary 바로 아래에 추가

@dataclass(frozen=True)
class TaxEstimate:
    """세금/세금금고에 보여줄 '추정치' 묶음.

    - 포함수입: IncomeLabel.status != 'non_income' (unknown/blank는 포함으로 간주)
    - 업무경비: ExpenseLabel.status == 'business'
    - 예상세액(추정): max(0, 포함수입-업무경비) * tax_rate
    """

    month_key: str
    tax_rate: float

    income_included_krw: int
    expense_business_base_krw: int
    expense_business_krw: int
    receipt_reflected_expense_krw: int
    receipt_pending_expense_krw: int
    receipt_excluded_expense_krw: int
    receipt_consult_tax_review_expense_krw: int
    reflected_transaction_count: int
    pending_transaction_count: int
    estimated_profit_krw: int
    estimated_tax_krw: int

    income_sum_krw: int
    expense_sum_krw: int
    net_est_krw: int
    tax_est_before_withheld_krw: int
    local_tax_est_krw: int
    withheld_est_krw: int
    withholding_base_krw: int
    withholding_mode: str
    withheld_tax_input_annual_krw: int
    prepaid_tax_input_annual_krw: int
    annual_tax_credit_input_krw: int
    has_withheld_tax_input: bool
    has_prepaid_tax_input: bool
    income_classification: str
    high_confidence_missing_fields: tuple[str, ...]
    exact_ready_missing_fields: tuple[str, ...]
    tax_due_est_krw: int

    income_source_code: str
    income_source_label: str
    income_source_year: int | None
    income_source_target_year: int
    income_override_applied: bool

    buffer_total_krw: int
    buffer_target_krw: int
    buffer_shortage_krw: int
    tax_due_before_receipt_effects_krw: int
    buffer_target_before_receipt_effects_krw: int
    tax_delta_from_receipts_krw: int
    buffer_delta_from_receipts_krw: int
    official_verified_withholding_tax_krw: int
    official_verified_paid_tax_krw: int
    official_tax_reference_date: date | None
    tax_due_before_official_adjustment_krw: int
    tax_delta_from_official_data_krw: int
    buffer_delta_from_official_data_krw: int
    official_data_applied: bool
    official_data_confidence_label: str
    official_data_effect_messages: tuple[str, ...]
    official_data_applied_documents: tuple[dict[str, Any], ...]
    official_calculable: bool
    official_block_reason: str
    accuracy_level: str
    official_taxable_income_annual_krw: int
    tax_calculation_mode: str
    is_limited_estimate: bool
    limited_estimate_reason: str
    taxable_income_input_source: str
    taxable_income_used_annual_krw: int
    warnings: tuple[str, ...]
    applied_flags: tuple[str, ...]


_TAX_BLOCK_REASON_DETAILS = {
    TAX_REASON_MISSING_TAXABLE_INCOME: "고급 입력(과세표준) 누락으로 exact_ready 판정을 할 수 없어요.",
    TAX_REASON_PROXY_FROM_ANNUAL_INCOME: "과세표준 직접 입력 없이 총수입/업무지출 기반으로 추정했어요.",
    TAX_REASON_MISSING_INCOME_CLASSIFICATION: "소득 구분 입력이 없어 신뢰도 높은 계산으로 판정할 수 없어요.",
    TAX_REASON_MISSING_WITHHELD_TAX: "원천징수/기납부 입력이 없어 차감값을 보수적으로 처리했어요.",
    TAX_REASON_MISSING_PREPAID_TAX: "중간예납 정보가 없어 실제 납부세액과 차이가 날 수 있어요.",
    TAX_REASON_ESTIMATE_UNAVAILABLE: "세금 계산 서비스를 불러오지 못해 기본 추정으로 계산했어요.",
    TAX_REASON_INSUFFICIENT_PROFILE_INPUTS: "입력 정보가 부족해 보수 추정으로 계산했어요.",
}

TAX_REQUIRED_FIELD_LABELS = {
    "official_taxable_income_annual_krw": "연 과세표준(고급 입력)",
    "income_classification": "소득 유형",
    "annual_gross_income_krw": "총수입",
    "annual_deductible_expense_krw": "업무 관련 지출",
    "withheld_tax_annual_krw": "이미 떼인 세금(원천징수)",
    "prepaid_tax_annual_krw": "이미 낸 세금(기납부)",
    "tax_basic_inputs_confirmed": "기본 입력 확인/저장",
    "tax_advanced_input_confirmed": "고급 입력 확인/저장",
    "withholding_3_3": "원천징수 여부",
    "industry_group": "업종",
    "tax_type": "과세유형",
    "prev_income_band": "전년도 수입 구간",
}


def _derive_tax_reason_code(
    est: TaxEstimate,
    *,
    mode: str,
    accuracy_level: str,
    raw_reason: str,
    is_limited: bool,
) -> str:
    reason = normalize_tax_reason(raw_reason, fallback=TAX_REASON_INSUFFICIENT_PROFILE_INPUTS)
    mode_norm = str(mode or "").strip().lower()
    level_norm = str(accuracy_level or "").strip().lower()
    flags = {str(v).strip().lower() for v in tuple(getattr(est, "applied_flags", ()) or ()) if str(v).strip()}

    if mode_norm == "blocked":
        high_missing = {str(v).strip() for v in (getattr(est, "high_confidence_missing_fields", ()) or ()) if str(v).strip()}
        if "income_classification" in high_missing:
            return TAX_REASON_MISSING_INCOME_CLASSIFICATION
        if "withheld_tax_annual_krw" in high_missing:
            return TAX_REASON_MISSING_WITHHELD_TAX
        if "prepaid_tax_annual_krw" in high_missing:
            return TAX_REASON_MISSING_PREPAID_TAX
        if reason == TAX_REASON_INSUFFICIENT_PROFILE_INPUTS:
            return TAX_REASON_MISSING_TAXABLE_INCOME
        return reason

    if mode_norm == "limited_proxy" or bool(is_limited):
        source = str(getattr(est, "taxable_income_input_source", "") or "").strip().lower()
        if source in {"profile_gross_income_proxy", "profile_income_expense_proxy", "income_hybrid_total_income_proxy", "monthly_profit_annualized_proxy"}:
            return TAX_REASON_PROXY_FROM_ANNUAL_INCOME
        if reason in {TAX_REASON_INSUFFICIENT_PROFILE_INPUTS, TAX_REASON_MISSING_TAXABLE_INCOME}:
            return TAX_REASON_PROXY_FROM_ANNUAL_INCOME
        return reason

    if mode_norm == "official_exact" and level_norm == "high_confidence":
        withholding_mode = str(getattr(est, "withholding_mode", "") or "").strip().lower()
        annual_credit = int(max(0, int(getattr(est, "annual_tax_credit_input_krw", 0) or 0)))
        withheld_input = bool(getattr(est, "has_withheld_tax_input", False))
        prepaid_input = bool(getattr(est, "has_prepaid_tax_input", False))
        if "profile_incomplete" in flags:
            return TAX_REASON_INSUFFICIENT_PROFILE_INPUTS
        if withholding_mode in {"heuristic", "unknown", "not_applied"} and annual_credit <= 0 and (not withheld_input):
            return TAX_REASON_MISSING_WITHHELD_TAX
        if withholding_mode == "profile_annual_credit" and withheld_input and (not prepaid_input):
            return TAX_REASON_MISSING_PREPAID_TAX
        return TAX_REASON_OK

    if mode_norm == "official_exact":
        income_classification = str(getattr(est, "income_classification", "unknown") or "unknown").strip().lower()
        if level_norm == "limited":
            if income_classification == "unknown":
                return TAX_REASON_MISSING_INCOME_CLASSIFICATION
            if not bool(getattr(est, "has_withheld_tax_input", False)):
                return TAX_REASON_MISSING_WITHHELD_TAX
            if not bool(getattr(est, "has_prepaid_tax_input", False)):
                return TAX_REASON_MISSING_PREPAID_TAX
            return TAX_REASON_INSUFFICIENT_PROFILE_INPUTS
        return TAX_REASON_OK

    return reason


def _build_tax_input_recovery_plan(est: TaxEstimate, reason: str) -> dict[str, list[str]]:
    high_missing = list(getattr(est, "high_confidence_missing_fields", ()) or ())
    exact_missing = list(getattr(est, "exact_ready_missing_fields", ()) or ())
    missing_fields = list(dict.fromkeys([str(k).strip() for k in [*high_missing, *exact_missing] if str(k).strip()]))

    auto_fillable_fields: list[str] = []
    low_confidence_inferable_fields: list[str] = []
    needs_user_input_fields: list[str] = []

    for field in missing_fields:
        if field in {"annual_gross_income_krw", "annual_deductible_expense_krw"}:
            auto_fillable_fields.append(field)
            needs_user_input_fields.append(field)
            continue
        if field == "official_taxable_income_annual_krw":
            low_confidence_inferable_fields.append(field)
            needs_user_input_fields.append(field)
            continue
        needs_user_input_fields.append(field)

    reason_norm = str(reason or "").strip().lower()
    if reason_norm in {TAX_REASON_PROXY_FROM_ANNUAL_INCOME, TAX_REASON_MISSING_TAXABLE_INCOME}:
        for key in ("annual_gross_income_krw", "annual_deductible_expense_krw"):
            if key not in auto_fillable_fields:
                auto_fillable_fields.append(key)
        if "official_taxable_income_annual_krw" not in low_confidence_inferable_fields:
            low_confidence_inferable_fields.append("official_taxable_income_annual_krw")
    if reason_norm == TAX_REASON_MISSING_INCOME_CLASSIFICATION and "income_classification" not in needs_user_input_fields:
        needs_user_input_fields.append("income_classification")
    if reason_norm == TAX_REASON_MISSING_WITHHELD_TAX and "withheld_tax_annual_krw" not in needs_user_input_fields:
        needs_user_input_fields.append("withheld_tax_annual_krw")
    if reason_norm == TAX_REASON_MISSING_PREPAID_TAX and "prepaid_tax_annual_krw" not in needs_user_input_fields:
        needs_user_input_fields.append("prepaid_tax_annual_krw")

    return {
        "auto_fillable_fields": auto_fillable_fields,
        "low_confidence_inferable_fields": low_confidence_inferable_fields,
        "needs_user_input_fields": needs_user_input_fields,
    }


def build_tax_result_meta(est: TaxEstimate | None) -> dict[str, Any]:
    """세금 추정 결과를 UI에서 안전하게 설명하기 위한 상태 메타."""
    if est is None:
        return {
            "level": "limited",
            "accuracy_level": "limited",
            "label": "제한된 추정",
            "message": "세금 계산 정보를 불러오지 못해 보수적으로 추정했어요.",
            "reason": TAX_REASON_ESTIMATE_UNAVAILABLE,
            "detail": "잠시 후 다시 확인해 주세요.",
            "is_limited": True,
            "mode": "unknown",
            "official_calculable": False,
            "required_inputs": {
                "high_confidence_missing_fields": [
                    "income_classification",
                    "annual_gross_income_krw",
                    "annual_deductible_expense_krw",
                    "withheld_tax_annual_krw",
                    "prepaid_tax_annual_krw",
                    "tax_basic_inputs_confirmed",
                ],
                "exact_ready_missing_fields": [
                    "income_classification",
                    "annual_gross_income_krw",
                    "annual_deductible_expense_krw",
                    "withheld_tax_annual_krw",
                    "prepaid_tax_annual_krw",
                    "tax_basic_inputs_confirmed",
                    "official_taxable_income_annual_krw",
                    "tax_advanced_input_confirmed",
                ],
                "high_confidence_inputs_ready": False,
                "exact_ready_inputs_ready": False,
            },
            "auto_fillable_fields": ["annual_gross_income_krw", "annual_deductible_expense_krw"],
            "low_confidence_inferable_fields": ["official_taxable_income_annual_krw"],
            "needs_user_input_fields": [
                "income_classification",
                "annual_gross_income_krw",
                "annual_deductible_expense_krw",
                "withheld_tax_annual_krw",
                "prepaid_tax_annual_krw",
                "tax_basic_inputs_confirmed",
            ],
            "official_data_applied": False,
            "official_data_confidence_label": "low",
            "official_tax_reference_date": None,
            "tax_delta_from_official_data_krw": 0,
            "official_effect_messages": [],
        }

    mode = str(getattr(est, "tax_calculation_mode", "unknown") or "unknown")
    official_calculable = bool(getattr(est, "official_calculable", False))
    is_limited = bool(getattr(est, "is_limited_estimate", False))
    accuracy_level = str(getattr(est, "accuracy_level", "") or "").strip().lower()

    if accuracy_level not in {"exact_ready", "high_confidence", "limited", "blocked"}:
        if mode == "blocked":
            accuracy_level = "blocked"
        elif mode == "limited_proxy" or is_limited:
            accuracy_level = "limited"
        elif mode == "official_exact" and official_calculable:
            accuracy_level = "high_confidence"
        else:
            accuracy_level = "limited"

    reason = _derive_tax_reason_code(
        est,
        mode=mode,
        accuracy_level=accuracy_level,
        raw_reason=str(getattr(est, "official_block_reason", "") or ""),
        is_limited=is_limited,
    )
    detail = str(_TAX_BLOCK_REASON_DETAILS.get(reason) or "")
    required_inputs = {
        "high_confidence_missing_fields": list(getattr(est, "high_confidence_missing_fields", ()) or ()),
        "exact_ready_missing_fields": list(getattr(est, "exact_ready_missing_fields", ()) or ()),
        "high_confidence_inputs_ready": not bool(getattr(est, "high_confidence_missing_fields", ()) or ()),
        "exact_ready_inputs_ready": not bool(getattr(est, "exact_ready_missing_fields", ()) or ()),
    }
    recovery_plan = _build_tax_input_recovery_plan(est, reason)
    official_data_meta = {
        "official_data_applied": bool(getattr(est, "official_data_applied", False)),
        "official_data_confidence_label": str(getattr(est, "official_data_confidence_label", "low") or "low"),
        "official_tax_reference_date": (
            getattr(est, "official_tax_reference_date", None).isoformat()
            if getattr(est, "official_tax_reference_date", None)
            else None
        ),
        "tax_delta_from_official_data_krw": int(getattr(est, "tax_delta_from_official_data_krw", 0) or 0),
        "official_effect_messages": list(getattr(est, "official_data_effect_messages", ()) or ()),
    }

    if mode == "official_exact" and official_calculable and accuracy_level in {"exact_ready", "high_confidence"}:
        return {
            "level": "normal",
            "accuracy_level": accuracy_level,
            "label": ("정밀 추정" if accuracy_level == "exact_ready" else "고신뢰 추정"),
            "message": "공식 기준 일부를 반영한 추정치예요. 실제 신고세액과는 차이가 있을 수 있어요.",
            "reason": reason or TAX_REASON_OK,
            "detail": "",
            "is_limited": False,
            "mode": mode,
            "official_calculable": True,
            "required_inputs": required_inputs,
            "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
            "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
            "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
            **official_data_meta,
        }

    if mode == "official_exact" and official_calculable:
        return {
            "level": "limited",
            "accuracy_level": "limited",
            "label": "제한된 추정",
            "message": "공식 계산은 가능하지만 99% 정확도 필수 입력이 부족해 제한된 추정으로 표시해요.",
            "reason": reason or TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
            "detail": detail or "총수입/업무지출/원천·기납부/소득유형을 저장해 주세요.",
            "is_limited": True,
            "mode": mode,
            "official_calculable": True,
            "required_inputs": required_inputs,
            "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
            "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
            "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
            **official_data_meta,
        }

    if (mode == "limited_proxy" or is_limited) and accuracy_level == "high_confidence":
        return {
            "level": "normal",
            "accuracy_level": "high_confidence",
            "label": "확인 기반 추정",
            "message": "기본 입력을 직접 확인해 저장한 고신뢰 추정치예요. 실제 신고세액과는 차이가 있을 수 있어요.",
            "reason": reason or TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
            "detail": detail or "정밀 모드에서 과세표준을 직접 입력하면 exact_ready로 올릴 수 있어요.",
            "is_limited": False,
            "mode": mode,
            "official_calculable": False,
            "required_inputs": required_inputs,
            "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
            "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
            "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
            **official_data_meta,
        }

    if mode == "limited_proxy" or is_limited:
        return {
            "level": "limited",
            "accuracy_level": accuracy_level,
            "label": "제한된 추정",
            "message": "입력 정보가 부족해 보수적으로 추정했어요. 실제보다 낮게 보일 수 있어요.",
            "reason": reason or TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
            "detail": detail or "기본 입력을 저장하면 high_confidence로, 고급 입력까지 저장하면 exact_ready로 올릴 수 있어요.",
            "is_limited": True,
            "mode": mode,
            "official_calculable": False,
            "required_inputs": required_inputs,
            "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
            "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
            "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
            **official_data_meta,
        }

    if mode == "blocked":
        return {
            "level": "blocked",
            "accuracy_level": "blocked",
            "label": "계산 제한",
            "message": "공식 계산에 필요한 입력이 부족해 세금이 0원 또는 낮게 보일 수 있어요.",
            "reason": reason or TAX_REASON_MISSING_TAXABLE_INCOME,
            "detail": detail or "소득 유형과 기본 입력(총수입/업무지출/원천·기납부)을 먼저 저장해 주세요.",
            "is_limited": True,
            "mode": mode,
            "official_calculable": False,
            "required_inputs": required_inputs,
            "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
            "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
            "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
            **official_data_meta,
        }

    return {
        "level": "limited",
        "accuracy_level": accuracy_level,
        "label": "제한된 추정",
        "message": "입력 또는 기준 정보가 부족해 제한된 추정치로 표시하고 있어요.",
        "reason": reason or TAX_REASON_INSUFFICIENT_PROFILE_INPUTS,
        "detail": detail,
        "is_limited": True,
        "mode": mode,
        "official_calculable": official_calculable,
        "required_inputs": required_inputs,
        "auto_fillable_fields": list(recovery_plan.get("auto_fillable_fields") or []),
        "low_confidence_inferable_fields": list(recovery_plan.get("low_confidence_inferable_fields") or []),
        "needs_user_input_fields": list(recovery_plan.get("needs_user_input_fields") or []),
        **official_data_meta,
    }


def build_tax_recovery_cta(
    tax_result_meta: dict[str, Any] | None,
    *,
    recovery_url: str,
) -> dict[str, Any]:
    meta = dict(tax_result_meta or {})
    accuracy_level = str(meta.get("accuracy_level") or "limited").strip().lower()
    blocked = accuracy_level == "blocked"
    limited = accuracy_level == "limited"
    show = blocked or limited
    required_inputs = dict(meta.get("required_inputs") or {})
    missing_fields = list(
        dict.fromkeys(
            [
                *[str(v) for v in (meta.get("needs_user_input_fields") or []) if str(v).strip()],
                *[str(v) for v in (required_inputs.get("exact_ready_missing_fields") or []) if str(v).strip()],
                *[str(v) for v in (required_inputs.get("high_confidence_missing_fields") or []) if str(v).strip()],
            ]
        )
    )
    missing_labels = [TAX_REQUIRED_FIELD_LABELS.get(field, field) for field in missing_fields]
    if blocked:
        title = "세금 계산에 필요한 정보 입력하기"
        description = "필수 입력을 완료하면 blocked 상태에서 벗어나 정확한 추정으로 전환돼요."
        action_label = "세금 필수 입력하기"
    elif limited:
        title = "세금 정확도 높이기"
        description = "누락 입력을 채우면 limited 추정에서 high/exact로 올릴 수 있어요."
        action_label = "세금 입력 보완하기"
    else:
        title = ""
        description = ""
        action_label = ""
    return {
        "show": show,
        "blocked": blocked,
        "limited": limited,
        "accuracy_level": accuracy_level,
        "title": title,
        "description": description,
        "action_label": action_label,
        "url": str(recovery_url or "/dashboard/profile"),
        "missing_fields": missing_fields,
        "missing_labels": missing_labels,
    }


def _resolve_taxable_income_input(
    *,
    profile: dict,
    override_annual_total_income_krw: int,
    annual_profit_est_krw: int,
) -> dict[str, int | str | bool]:
    def _first_positive_from_profile(keys: tuple[str, ...]) -> int:
        for key in keys:
            raw = profile.get(key)
            candidate = _parse_preview_amount(str(raw)) if raw is not None else 0
            if candidate > 0:
                return int(candidate)
        return 0

    official_taxable_income_annual_krw = 0
    taxable_candidates = (
        "official_taxable_income_annual_krw",
        "taxable_income_annual_krw",
        "taxable_base_annual_krw",
        "annual_taxable_income_krw",
    )
    for key in taxable_candidates:
        raw = profile.get(key)
        candidate = _parse_preview_amount(str(raw)) if raw is not None else 0
        if candidate > 0:
            official_taxable_income_annual_krw = int(candidate)
            break

    if official_taxable_income_annual_krw > 0:
        return {
            "official_taxable_income_annual_krw": int(official_taxable_income_annual_krw),
            "taxable_income_used_annual_krw": int(official_taxable_income_annual_krw),
            "official_input_satisfied": True,
            "is_limited_estimate": False,
            "limited_estimate_reason": "",
            "taxable_income_input_source": "profile_taxable_income",
            "block_reason": "",
        }

    annual_gross_income = _first_positive_from_profile(TAX_ANNUAL_GROSS_INCOME_KEYS)
    annual_deductible_expense = _first_positive_from_profile(TAX_ANNUAL_EXPENSE_KEYS)
    if annual_gross_income > 0:
        annual_taxable_proxy = annual_gross_income
        proxy_source = "profile_gross_income_proxy"
        if annual_deductible_expense > 0 and annual_gross_income > annual_deductible_expense:
            annual_taxable_proxy = int(max(0, annual_gross_income - annual_deductible_expense))
            proxy_source = "profile_income_expense_proxy"
        if annual_taxable_proxy > 0:
            return {
                "official_taxable_income_annual_krw": 0,
                "taxable_income_used_annual_krw": int(annual_taxable_proxy),
                "official_input_satisfied": False,
                "is_limited_estimate": True,
                "limited_estimate_reason": TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
                "taxable_income_input_source": str(proxy_source),
                "block_reason": TAX_REASON_MISSING_TAXABLE_INCOME,
            }

    override_income = max(0, int(override_annual_total_income_krw or 0))
    if override_income > 0:
        return {
            "official_taxable_income_annual_krw": 0,
            "taxable_income_used_annual_krw": int(override_income),
            "official_input_satisfied": False,
            "is_limited_estimate": True,
            "limited_estimate_reason": TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
            "taxable_income_input_source": "income_hybrid_total_income_proxy",
            "block_reason": TAX_REASON_MISSING_TAXABLE_INCOME,
        }

    annual_profit = max(0, int(annual_profit_est_krw or 0))
    if annual_profit > 0:
        return {
            "official_taxable_income_annual_krw": 0,
            "taxable_income_used_annual_krw": int(annual_profit),
            "official_input_satisfied": False,
            "is_limited_estimate": True,
            "limited_estimate_reason": TAX_REASON_PROXY_FROM_ANNUAL_INCOME,
            "taxable_income_input_source": "monthly_profit_annualized_proxy",
            "block_reason": TAX_REASON_MISSING_TAXABLE_INCOME,
        }

    return {
        "official_taxable_income_annual_krw": 0,
        "taxable_income_used_annual_krw": 0,
        "official_input_satisfied": False,
        "is_limited_estimate": False,
        "limited_estimate_reason": "",
        "taxable_income_input_source": "missing",
        "block_reason": TAX_REASON_MISSING_TAXABLE_INCOME,
    }


def _resolve_annual_tax_credit_input(profile: dict) -> tuple[int, int, int]:
    def _first_non_negative(keys: tuple[str, ...]) -> int:
        for key in keys:
            raw = profile.get(key)
            if raw is None:
                continue
            raw_text = str(raw).replace(",", "").replace("원", "").strip()
            if not raw_text:
                continue
            candidate = _parse_preview_amount(raw_text)
            if candidate >= 0:
                return int(max(0, candidate))
        return 0

    withheld = int(max(0, _first_non_negative(TAX_WITHHELD_TAX_ANNUAL_KEYS)))
    prepaid = int(max(0, _first_non_negative(TAX_PREPAID_TAX_ANNUAL_KEYS)))
    total = int(max(0, withheld + prepaid))
    return int(withheld), int(prepaid), int(total)


def _resolve_tax_accuracy_level(
    *,
    tax_calculation_mode: str,
    official_calculable: bool,
    is_limited_estimate: bool,
    high_confidence_inputs_ready: bool,
    exact_ready_inputs_ready: bool,
) -> str:
    mode = str(tax_calculation_mode or "").strip().lower()
    if mode == "blocked":
        return "blocked"
    if bool(exact_ready_inputs_ready) and bool(official_calculable):
        return "exact_ready"
    if bool(high_confidence_inputs_ready):
        return "high_confidence"
    if mode == "limited_proxy" or bool(is_limited_estimate):
        return "limited"
    if not bool(official_calculable):
        return "blocked"
    return "limited"


def _compute_monthly_tax_due_snapshot(
    *,
    profile: dict[str, Any],
    ref_year: int,
    annual_profit_est_krw: int,
    income_included_krw: int,
    override_annual_total_income_krw: int,
    annual_receipt_reflected_expense_krw: int,
    annual_tax_credit_input_krw: int,
    withholding_state: str,
    withholding_heuristic_base_krw: int,
) -> dict[str, Any]:
    taxable_resolution = _resolve_taxable_income_input(
        profile=profile,
        override_annual_total_income_krw=int(max(0, override_annual_total_income_krw or 0)),
        annual_profit_est_krw=int(max(0, annual_profit_est_krw or 0)),
    )
    taxable_income_annual_krw = int(taxable_resolution.get("official_taxable_income_annual_krw") or 0)
    taxable_income_used_annual_krw = int(taxable_resolution.get("taxable_income_used_annual_krw") or 0)
    official_input_satisfied = bool(taxable_resolution.get("official_input_satisfied"))
    is_limited_estimate = bool(taxable_resolution.get("is_limited_estimate"))
    limited_estimate_reason = str(taxable_resolution.get("limited_estimate_reason") or "")
    taxable_income_input_source = str(taxable_resolution.get("taxable_income_input_source") or "missing")
    block_reason = str(taxable_resolution.get("block_reason") or TAX_REASON_MISSING_TAXABLE_INCOME)
    reflected_expense_annual = int(max(0, annual_receipt_reflected_expense_krw or 0))
    if reflected_expense_annual > 0 and not official_input_satisfied and taxable_income_used_annual_krw > 0:
        taxable_income_used_annual_krw = max(0, int(taxable_income_used_annual_krw) - int(reflected_expense_annual))

    official_core = compute_tax_official_core(
        taxable_income_annual_krw=taxable_income_used_annual_krw,
        target_year=ref_year,
    )
    tax_core_calculable = bool(official_core.calculable)
    official_calculable = bool(official_input_satisfied and tax_core_calculable)
    tax_calculation_mode = "blocked"
    if official_calculable:
        tax_calculation_mode = "official_exact"
    elif is_limited_estimate and tax_core_calculable:
        tax_calculation_mode = "limited_proxy"

    annual_national_tax = int(official_core.national_tax_annual_krw)
    annual_local_tax = int(official_core.local_tax_annual_krw)
    tax_before_withheld = max(0, int(round((annual_national_tax + annual_local_tax) / 12)))
    local_tax = max(0, int(round(annual_local_tax / 12)))

    withheld_est = 0
    withholding_base = 0
    withholding_mode = "not_applied"
    if tax_core_calculable:
        if int(annual_tax_credit_input_krw or 0) > 0:
            withheld_est = int(round(int(annual_tax_credit_input_krw) / 12))
            withholding_mode = "profile_annual_credit"
        elif withholding_state == "yes":
            withholding_base = int(income_included_krw or 0)
            withheld_est = int(round(int(withholding_base) * 0.033))
            withholding_mode = "profile_yes"
        elif withholding_state == "no":
            withholding_mode = "profile_no"
        elif int(withholding_heuristic_base_krw or 0) > 0:
            withholding_base = int(withholding_heuristic_base_krw or 0)
            withheld_est = int(round(int(withholding_base) * 0.033))
            withholding_mode = "heuristic"
        else:
            withholding_mode = "not_applied"
    else:
        annual_national_tax = 0
        annual_local_tax = 0
        tax_before_withheld = 0
        local_tax = 0
        withheld_est = 0
        withholding_base = 0
        withholding_mode = "blocked"
        taxable_income_input_source = "missing"
        tax_calculation_mode = "blocked"
        is_limited_estimate = False
        limited_estimate_reason = ""

    if withheld_est > tax_before_withheld:
        withheld_est = int(tax_before_withheld)
    tax_due_est = max(0, int(tax_before_withheld) - int(withheld_est))

    if official_calculable:
        official_block_reason = ""
    elif is_limited_estimate and tax_core_calculable:
        official_block_reason = str(limited_estimate_reason or block_reason or TAX_REASON_MISSING_TAXABLE_INCOME)
    else:
        official_block_reason = str(official_core.reason or block_reason or TAX_REASON_MISSING_TAXABLE_INCOME)

    return {
        "taxable_income_annual_krw": int(taxable_income_annual_krw),
        "taxable_income_used_annual_krw": int(taxable_income_used_annual_krw),
        "official_calculable": bool(official_calculable),
        "tax_core_calculable": bool(tax_core_calculable),
        "tax_calculation_mode": str(tax_calculation_mode),
        "is_limited_estimate": bool(is_limited_estimate),
        "limited_estimate_reason": str(limited_estimate_reason),
        "taxable_income_input_source": str(taxable_income_input_source),
        "tax_before_withheld_krw": int(tax_before_withheld),
        "local_tax_est_krw": int(local_tax),
        "withheld_est_krw": int(withheld_est),
        "withholding_base_krw": int(withholding_base),
        "withholding_mode": str(withholding_mode),
        "tax_due_est_krw": int(tax_due_est),
        "official_block_reason": str(official_block_reason),
        "block_reason": str(block_reason),
        "annual_national_tax_krw": int(annual_national_tax),
        "annual_local_tax_krw": int(annual_local_tax),
    }


def compute_tax_estimate(
    user_pk: int,
    month_key: str | None = None,
    *,
    prefer_monthly_signal: bool = False,
) -> TaxEstimate:
    """세금 금고(권장액/부족액)를 '업무경비 반영' 기준으로 계산."""
    month_key = month_key or _month_key_now()
    start_dt, end_dt = _month_range_kst_naive(month_key)

    settings = _get_settings(user_pk)

    tax_rate = float(getattr(settings, "default_tax_rate", 0.15) or 0.15)
    if tax_rate > 1:
        tax_rate = tax_rate / 100.0
    tax_rate = max(0.0, min(tax_rate, 0.95))

    income_rows = (
        db.session.query(
            Transaction.amount_krw,
            Transaction.counterparty,
            Transaction.memo,
            IncomeLabel.status,
        )
        .select_from(Transaction)
        .outerjoin(
            IncomeLabel,
            (IncomeLabel.transaction_id == Transaction.id) & (IncomeLabel.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )

    # 포함 수입(비수입 제외): unknown/빈 값은 포함으로 간주
    income_included = 0
    withholding_heuristic_base = 0
    for amount, counterparty, memo, status in income_rows:
        amt = int(amount or 0)
        if status == "non_income":
            continue
        income_included += amt
        text = f"{counterparty or ''} {memo or ''}"
        if ("3.3" in text) or ("원천" in text):
            withholding_heuristic_base += amt

    income_source_code = "auto"
    income_source_label = "자동 추정(연동)"
    income_source_year: int | None = None
    income_source_target_year = 0
    income_override_applied = False
    override_agg: dict[str, int] = {}
    try:
        override_pick = pick_income_override_for_month(
            user_pk=int(user_pk),
            month_key=month_key,
            purpose="tax",
        )
        income_source_target_year = int(override_pick.get("target_year") or 0)
        if bool(override_pick.get("applied")) and isinstance(override_pick.get("entry"), dict):
            override_agg = aggregate_income_override(dict(override_pick.get("entry") or {}))
            annual_total_income = int(max(0, override_agg.get("annual_total_income_krw") or 0))
            if annual_total_income > 0:
                if prefer_monthly_signal:
                    income_source_code = "auto"
                    income_source_label = "자동 추정(연동·월 반영)"
                    income_source_year = None
                    income_override_applied = False
                else:
                    income_included = int(max(0, round(annual_total_income / 12)))
                    income_source_code = "user_input"
                    income_source_label = "사용자 입력(확정)"
                    income_source_year = (
                        int(override_pick.get("used_year"))
                        if override_pick.get("used_year") is not None
                        else income_source_target_year
                    )
                    income_override_applied = True
    except Exception:
        income_source_code = "auto"
        income_source_label = "자동 추정(연동)"
        income_source_year = None
        income_override_applied = False

    # 업무 경비(확정): 사용자가 이미 business로 분류한 거래
    expense_business_base = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .select_from(Transaction)
        .outerjoin(
            ExpenseLabel,
            (ExpenseLabel.transaction_id == Transaction.id) & (ExpenseLabel.user_pk == user_pk),
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(ExpenseLabel.status == "business")
        .scalar()
    ) or 0
    receipt_tax_effects = compute_receipt_tax_effects_for_month(
        db.session,
        user_pk=int(user_pk),
        month_key=month_key,
    )
    receipt_reflected_expense = int(receipt_tax_effects.reflected_expense_krw)
    expense_business = int(expense_business_base) + int(receipt_reflected_expense)

    est_profit = max(0, int(income_included) - int(expense_business))
    base_est_profit = max(0, int(income_included) - int(expense_business_base))
    annual_profit_est = max(0, int(est_profit * 12))
    annual_profit_before_receipt_effects = max(0, int(base_est_profit * 12))
    ref_year = _tax_reference_year(month_key)
    local_ratio = float(get_tax_reference_snapshot(ref_year).local_income_tax_ratio)

    profile = get_tax_profile(user_pk)
    official_data_effects = collect_official_data_effects_for_user(
        db.session,
        user_pk=int(user_pk),
        month_key=month_key,
        profile_json=profile,
    )
    profile_complete = bool(is_tax_profile_complete(profile))
    tax_input_eval = evaluate_tax_required_inputs(profile)
    has_taxable_income_input = bool(tax_input_eval.get("has_taxable_income"))
    has_income_classification = bool(tax_input_eval.get("has_income_classification"))
    has_withholding_declared = bool(tax_input_eval.get("has_withholding_declared"))
    has_withheld_tax_input = bool(tax_input_eval.get("has_withheld_tax_input"))
    has_prepaid_tax_input = bool(tax_input_eval.get("has_prepaid_tax_input"))
    income_classification = str(tax_input_eval.get("income_classification") or "unknown")
    high_confidence_missing_fields = list(tax_input_eval.get("high_confidence_missing_fields") or ())
    exact_ready_missing_fields = list(tax_input_eval.get("exact_ready_missing_fields") or ())
    withholding_state = profile.get("withholding_3_3")
    if withholding_state in (None, ""):
        withholding_state = profile.get("withholding_33")
    withholding_state = str(withholding_state or "unknown")
    ko_to_en = {"있음": "yes", "없음": "no", "모름": "unknown"}
    withholding_state = ko_to_en.get(withholding_state, withholding_state)
    if withholding_state not in TAX_WITHHOLDING_33:
        withholding_state = "unknown"

    warnings: list[str] = []
    applied_flags: list[str] = []
    withheld_est = 0
    withholding_base = 0
    withholding_mode = "not_applied"
    (
        manual_withheld_tax_input_annual_krw,
        manual_prepaid_tax_input_annual_krw,
        manual_annual_tax_credit_input_krw,
    ) = _resolve_annual_tax_credit_input(profile)
    withheld_tax_input_annual_krw = int(manual_withheld_tax_input_annual_krw)
    prepaid_tax_input_annual_krw = int(manual_prepaid_tax_input_annual_krw)
    annual_tax_credit_input_krw = int(manual_annual_tax_credit_input_krw)
    if official_data_effects.tax.verified_withholding_applied:
        withheld_tax_input_annual_krw = int(official_data_effects.tax.verified_withholding_tax_krw)
    if official_data_effects.tax.verified_paid_tax_applied:
        prepaid_tax_input_annual_krw = int(official_data_effects.tax.verified_paid_tax_krw)
    annual_tax_credit_input_krw = int(max(0, withheld_tax_input_annual_krw + prepaid_tax_input_annual_krw))
    has_withheld_tax_input = bool(has_withheld_tax_input or official_data_effects.tax.verified_withholding_applied)
    has_prepaid_tax_input = bool(has_prepaid_tax_input or official_data_effects.tax.verified_paid_tax_applied)
    if has_withheld_tax_input and "withheld_tax_annual_krw" in high_confidence_missing_fields:
        high_confidence_missing_fields = [key for key in high_confidence_missing_fields if key != "withheld_tax_annual_krw"]
    if has_prepaid_tax_input and "prepaid_tax_annual_krw" in high_confidence_missing_fields:
        high_confidence_missing_fields = [key for key in high_confidence_missing_fields if key != "prepaid_tax_annual_krw"]
    if has_withheld_tax_input and "withheld_tax_annual_krw" in exact_ready_missing_fields:
        exact_ready_missing_fields = [key for key in exact_ready_missing_fields if key != "withheld_tax_annual_krw"]
    if has_prepaid_tax_input and "prepaid_tax_annual_krw" in exact_ready_missing_fields:
        exact_ready_missing_fields = [key for key in exact_ready_missing_fields if key != "prepaid_tax_annual_krw"]
    high_confidence_inputs_ready = bool(not high_confidence_missing_fields)
    exact_ready_inputs_ready = bool(not exact_ready_missing_fields)
    if not has_income_classification:
        warnings.append("소득 구성을 입력하면 세금 정확도가 올라가요.")
        applied_flags.append("income_classification_missing")
    if not has_withheld_tax_input:
        applied_flags.append("withheld_tax_input_missing")
    if not has_prepaid_tax_input:
        applied_flags.append("prepaid_tax_input_missing")

    override_annual_total_income = int(max(0, (override_agg or {}).get("annual_total_income_krw") or 0))
    if prefer_monthly_signal:
        # 캘린더 월 카드에서는 월 거래 신호가 사라지지 않도록
        # 연간 override 프록시 우선을 끄고 월 이익 연환산 프록시를 사용한다.
        override_annual_total_income = 0
    after_tax_snapshot = _compute_monthly_tax_due_snapshot(
        profile=profile,
        ref_year=ref_year,
        annual_profit_est_krw=annual_profit_est,
        income_included_krw=int(income_included),
        override_annual_total_income_krw=int(override_annual_total_income),
        annual_receipt_reflected_expense_krw=int(receipt_reflected_expense * 12),
        annual_tax_credit_input_krw=int(annual_tax_credit_input_krw),
        withholding_state=str(withholding_state),
        withholding_heuristic_base_krw=int(withholding_heuristic_base),
    )
    before_official_snapshot = _compute_monthly_tax_due_snapshot(
        profile=profile,
        ref_year=ref_year,
        annual_profit_est_krw=annual_profit_est,
        income_included_krw=int(income_included),
        override_annual_total_income_krw=int(override_annual_total_income),
        annual_receipt_reflected_expense_krw=int(receipt_reflected_expense * 12),
        annual_tax_credit_input_krw=int(manual_annual_tax_credit_input_krw),
        withholding_state=str(withholding_state),
        withholding_heuristic_base_krw=int(withholding_heuristic_base),
    )
    before_tax_snapshot = _compute_monthly_tax_due_snapshot(
        profile=profile,
        ref_year=ref_year,
        annual_profit_est_krw=annual_profit_before_receipt_effects,
        income_included_krw=int(income_included),
        override_annual_total_income_krw=int(override_annual_total_income),
        annual_receipt_reflected_expense_krw=0,
        annual_tax_credit_input_krw=int(annual_tax_credit_input_krw),
        withholding_state=str(withholding_state),
        withholding_heuristic_base_krw=int(withholding_heuristic_base),
    )

    taxable_income_annual_krw = int(after_tax_snapshot.get("taxable_income_annual_krw") or 0)
    taxable_income_used_annual_krw = int(after_tax_snapshot.get("taxable_income_used_annual_krw") or 0)
    official_calculable = bool(after_tax_snapshot.get("official_calculable"))
    tax_core_calculable = bool(after_tax_snapshot.get("tax_core_calculable"))
    is_limited_estimate = bool(after_tax_snapshot.get("is_limited_estimate"))
    limited_estimate_reason = str(after_tax_snapshot.get("limited_estimate_reason") or "")
    taxable_income_input_source = str(after_tax_snapshot.get("taxable_income_input_source") or "missing")
    block_reason = str(after_tax_snapshot.get("block_reason") or TAX_REASON_MISSING_TAXABLE_INCOME)
    tax_calculation_mode = str(after_tax_snapshot.get("tax_calculation_mode") or "blocked")
    tax_before_withheld = int(after_tax_snapshot.get("tax_before_withheld_krw") or 0)
    local_tax = int(after_tax_snapshot.get("local_tax_est_krw") or 0)
    withheld_est = int(after_tax_snapshot.get("withheld_est_krw") or 0)
    withholding_base = int(after_tax_snapshot.get("withholding_base_krw") or 0)
    withholding_mode = str(after_tax_snapshot.get("withholding_mode") or "not_applied")
    tax_due_est = int(after_tax_snapshot.get("tax_due_est_krw") or 0)
    tax_due_before_official_adjustment = int(before_official_snapshot.get("tax_due_est_krw") or 0)
    tax_due_before_receipt_effects = int(before_tax_snapshot.get("tax_due_est_krw") or 0)

    if tax_core_calculable:
        if official_calculable:
            applied_flags.append("official_tax_core_exact")
            warnings.append("과세표준 입력 기준으로 공식 세율을 적용했어요(추정).")
        elif is_limited_estimate:
            applied_flags.append(f"official_tax_core_limited:{taxable_income_input_source}")
            warnings.append("과세표준 입력이 없어 수입/이익 기반 보수 추정으로 계산했어요.")
        else:
            applied_flags.append("official_tax_core_available")
            warnings.append("공식 계산 결과를 적용했어요(추정).")
    else:
        tax_calculation_mode = "blocked"
        is_limited_estimate = False
        limited_estimate_reason = ""
        taxable_income_input_source = "missing"
        applied_flags.append(f"official_tax_core_blocked:{after_tax_snapshot.get('official_block_reason') or block_reason}")
        warnings.append("계산 불가: 공식 입력(과세표준 또는 공제 항목)이 부족해요.")

    if income_override_applied:
        warnings.append("세금 소득은 사용자 입력(확정) 기준을 우선 반영했어요(추정).")
        applied_flags.append("income_override_user_input")
    else:
        applied_flags.append("income_auto_estimate")

    if tax_core_calculable:
        if official_data_effects.tax.verified_withholding_applied or official_data_effects.tax.verified_paid_tax_applied:
            warnings.append("홈택스 공식 자료 기준으로 이미 빠진 세금을 월 환산 반영했어요.")
            applied_flags.append("official_data_tax_snapshot_applied")
            withholding_mode = "official_verified_snapshot"
        elif annual_tax_credit_input_krw > 0:
            warnings.append("연간 기납부/원천세액 입력을 월 환산해 차감했어요.")
            applied_flags.append("withholding_profile_annual_credit")
        elif withholding_state == "yes":
            applied_flags.append("withholding_profile_yes")
        elif withholding_state == "no":
            applied_flags.append("withholding_profile_no")
        else:
            if withholding_heuristic_base > 0:
                applied_flags.append("withholding_heuristic")
            else:
                warnings.append("원천징수 여부를 입력하면 더 정확해져요.")
                applied_flags.append("withholding_not_applied")
    else:
        applied_flags.append("withholding_skipped_official_block")

    if tax_core_calculable and int(income_included) >= 1_000_000 and int(tax_due_est) < int(income_included * 0.01):
        warnings.append("세액이 낮게 보이면 원천징수/경비 확정/기준 월을 확인해 주세요.")
        applied_flags.append("low_effective_tax_hint")
    if receipt_reflected_expense > 0:
        warnings.append(f"영수증 비용 반영 {int(receipt_reflected_expense):,}원을 추정 경비에 포함했어요.")
        applied_flags.append("receipt_reflected_high_likelihood")
    if int(receipt_tax_effects.pending_review_expense_krw) > 0:
        warnings.append(
            f"추가 확인이 필요한 영수증 {int(receipt_tax_effects.pending_review_expense_krw):,}원은 아직 세금에 반영하지 않았어요."
        )
        applied_flags.append("receipt_pending_review_exists")
    if int(receipt_tax_effects.consult_tax_review_expense_krw) > 0:
        applied_flags.append("receipt_consult_tax_review_exists")
    if tax_core_calculable:
        applied_flags.append("annualized_progressive_estimate")
    applied_flags.append(f"local_income_tax_ratio_{local_ratio:.2f}")

    other_income_state = str(profile.get("other_income") or "unknown")
    other_income_state = {"있음": "yes", "없음": "no", "모름": "unknown"}.get(other_income_state, other_income_state)
    if other_income_state == "yes":
        warnings.append("다른 소득이 있으면 실제 세액이 달라질 수 있어요.")
        applied_flags.append("other_income_yes")

    industry_group = str(profile.get("industry_group") or profile.get("industry") or "unknown")
    prev_income_band = str(profile.get("prev_income_band") or profile.get("prev_year_revenue_band") or "unknown")
    industry_unknown = (industry_group not in TAX_INDUSTRY_GROUPS) or (industry_group == "unknown")
    prev_income_unknown = (prev_income_band not in TAX_PREV_INCOME_BANDS) or (prev_income_band == "unknown")
    if industry_unknown or prev_income_unknown:
        warnings.append("업종/전년도 수입을 입력하면 더 정확해져요.")
        applied_flags.append("profile_accuracy_low")
    if profile_complete:
        applied_flags.append("profile_complete")
    else:
        applied_flags.append("profile_incomplete")

    buffer_total = (
        db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
        .filter(TaxBufferLedger.user_pk == user_pk)
        .scalar()
    ) or 0

    buffer_target = max(0, int(tax_due_est))
    buffer_shortage = max(0, buffer_target - int(buffer_total))
    official_block_reason = str(after_tax_snapshot.get("official_block_reason") or "")
    accuracy_level = _resolve_tax_accuracy_level(
        tax_calculation_mode=str(tax_calculation_mode),
        official_calculable=bool(official_calculable),
        is_limited_estimate=bool(is_limited_estimate),
        high_confidence_inputs_ready=bool(high_confidence_inputs_ready),
        exact_ready_inputs_ready=bool(exact_ready_inputs_ready),
    )
    tax_delta_from_receipts = int(tax_due_est) - int(tax_due_before_receipt_effects)
    buffer_delta_from_receipts = int(buffer_target) - int(tax_due_before_receipt_effects)
    tax_delta_from_official_data = int(tax_due_est) - int(tax_due_before_official_adjustment)
    buffer_delta_from_official_data = int(buffer_target) - int(tax_due_before_official_adjustment)
    official_data_applied = bool(
        official_data_effects.tax.verified_withholding_applied
        or official_data_effects.tax.verified_paid_tax_applied
    )

    return TaxEstimate(
        month_key=month_key,
        tax_rate=float(tax_rate),
        income_included_krw=int(income_included),
        expense_business_base_krw=int(expense_business_base),
        expense_business_krw=int(expense_business),
        receipt_reflected_expense_krw=int(receipt_reflected_expense),
        receipt_pending_expense_krw=int(receipt_tax_effects.pending_review_expense_krw),
        receipt_excluded_expense_krw=int(receipt_tax_effects.excluded_expense_krw),
        receipt_consult_tax_review_expense_krw=int(receipt_tax_effects.consult_tax_review_expense_krw),
        reflected_transaction_count=int(receipt_tax_effects.reflected_transaction_count),
        pending_transaction_count=int(receipt_tax_effects.pending_transaction_count),
        estimated_profit_krw=int(est_profit),
        estimated_tax_krw=int(tax_due_est),
        income_sum_krw=int(income_included),
        expense_sum_krw=int(expense_business),
        net_est_krw=int(est_profit),
        tax_est_before_withheld_krw=int(tax_before_withheld),
        local_tax_est_krw=int(local_tax),
        withheld_est_krw=int(withheld_est),
        withholding_base_krw=int(withholding_base),
        withholding_mode=str(withholding_mode),
        withheld_tax_input_annual_krw=int(withheld_tax_input_annual_krw),
        prepaid_tax_input_annual_krw=int(prepaid_tax_input_annual_krw),
        annual_tax_credit_input_krw=int(annual_tax_credit_input_krw),
        has_withheld_tax_input=bool(has_withheld_tax_input),
        has_prepaid_tax_input=bool(has_prepaid_tax_input),
        income_classification=str(income_classification),
        high_confidence_missing_fields=tuple(high_confidence_missing_fields),
        exact_ready_missing_fields=tuple(exact_ready_missing_fields),
        tax_due_est_krw=int(tax_due_est),
        income_source_code=str(income_source_code),
        income_source_label=str(income_source_label),
        income_source_year=(int(income_source_year) if income_source_year is not None else None),
        income_source_target_year=int(income_source_target_year),
        income_override_applied=bool(income_override_applied),
        buffer_total_krw=int(buffer_total),
        buffer_target_krw=int(buffer_target),
        buffer_shortage_krw=int(buffer_shortage),
        tax_due_before_receipt_effects_krw=int(tax_due_before_receipt_effects),
        buffer_target_before_receipt_effects_krw=int(tax_due_before_receipt_effects),
        tax_delta_from_receipts_krw=int(tax_delta_from_receipts),
        buffer_delta_from_receipts_krw=int(buffer_delta_from_receipts),
        official_verified_withholding_tax_krw=int(official_data_effects.verified_withholding_tax_krw),
        official_verified_paid_tax_krw=int(official_data_effects.verified_paid_tax_krw),
        official_tax_reference_date=official_data_effects.verified_tax_reference_date,
        tax_due_before_official_adjustment_krw=int(tax_due_before_official_adjustment),
        tax_delta_from_official_data_krw=int(tax_delta_from_official_data),
        buffer_delta_from_official_data_krw=int(buffer_delta_from_official_data),
        official_data_applied=official_data_applied,
        official_data_confidence_label=str(official_data_effects.official_data_confidence_level),
        official_data_effect_messages=tuple(official_data_effects.effect_messages),
        official_data_applied_documents=tuple(official_data_effects.applied_documents),
        official_calculable=bool(official_calculable),
        official_block_reason=str(official_block_reason),
        accuracy_level=str(accuracy_level),
        official_taxable_income_annual_krw=int(taxable_income_annual_krw),
        tax_calculation_mode=str(tax_calculation_mode),
        is_limited_estimate=bool(is_limited_estimate),
        limited_estimate_reason=str(limited_estimate_reason),
        taxable_income_input_source=str(taxable_income_input_source),
        taxable_income_used_annual_krw=int(taxable_income_used_annual_krw),
        warnings=tuple(warnings),
        applied_flags=tuple(applied_flags),
    )


def compute_risk_summary(
    user_pk: int,
    month_key: str | None = None,
    *,
    prefer_monthly_signal: bool = False,
) -> RiskSummary:
    month_key = month_key or _month_key_now()
    start_dt, end_dt = _month_range_kst_naive(month_key)
    tax_est = compute_tax_estimate(
        user_pk=user_pk,
        month_key=month_key,
        prefer_monthly_signal=prefer_monthly_signal,
    )

    gross_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    expenses = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    evidence_required = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement == "required")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    evidence_maybe = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement == "maybe")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    expense_review = (
        db.session.query(func.count(ExpenseLabel.id))
        .join(Transaction, Transaction.id == ExpenseLabel.transaction_id)
        .filter(ExpenseLabel.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(ExpenseLabel.status.in_(("mixed", "unknown")))
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    income_unknown = (
        db.session.query(func.count(IncomeLabel.id))
        .join(Transaction, Transaction.id == IncomeLabel.transaction_id)
        .filter(IncomeLabel.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(IncomeLabel.status == "unknown")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .scalar()
    ) or 0

    return RiskSummary(
        month_key=month_key,
        gross_income_krw=int(gross_income),
        expenses_krw=int(expenses),
        evidence_missing_required=int(evidence_required),
        evidence_missing_maybe=int(evidence_maybe),
        expense_needs_review=int(expense_review),
        income_unknown=int(income_unknown),
        buffer_total_krw=int(tax_est.buffer_total_krw),
        buffer_target_krw=int(tax_est.buffer_target_krw),
        buffer_shortage_krw=int(tax_est.buffer_shortage_krw),
    )


def refresh_recurring_candidates(
    user_pk: int,
    *,
    lookback_days: int = 90,
    min_samples: int = 3,
) -> int:
    """최근 거래에서 월간 정기 거래 후보를 계산/저장한다.

    - 같은 거래처(정규화) + 같은 입출 방향
    - 금액 편차 ±2% 내 패턴이 min_samples 이상
    - 90일 구간에서 월간 간격(약 24~38일) 패턴이 있으면 후보로 인정
    """
    end_dt = utcnow().replace(tzinfo=None)
    start_dt = end_dt - timedelta(days=max(30, int(lookback_days or 90)))
    min_samples = max(2, int(min_samples or 3))

    rows = (
        db.session.query(
            Transaction.direction,
            Transaction.counterparty,
            Transaction.amount_krw,
            Transaction.occurred_at,
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at <= end_dt)
        .filter(Transaction.amount_krw > 0)
        .filter(Transaction.counterparty.isnot(None))
        .all()
    )

    buckets: dict[tuple[str, str], list[tuple[str, int, datetime]]] = {}
    for direction, counterparty, amount_krw, occurred_at in rows:
        cp = str(counterparty or "").strip()
        key = normalize_counterparty_key(cp)
        if (direction not in ("in", "out")) or (not key) or (not occurred_at):
            continue
        buckets.setdefault((direction, key), []).append((cp, int(amount_krw or 0), occurred_at))

    candidates: list[RecurringCandidate] = []
    for (direction, key), items in buckets.items():
        if len(items) < min_samples:
            continue
        items.sort(key=lambda x: x[2])
        amounts = [max(0, int(v[1] or 0)) for v in items if int(v[1] or 0) > 0]
        if len(amounts) < min_samples:
            continue

        avg_amount = sum(amounts) / len(amounts)
        tolerance = max(1_000, int(round(avg_amount * 0.02)))
        stable_amount_cnt = sum(1 for a in amounts if abs(a - avg_amount) <= tolerance)
        if stable_amount_cnt < min_samples:
            continue

        dates = [v[2] for v in items]
        month_keys = {d.strftime("%Y-%m") for d in dates}
        if len(month_keys) < 2:
            continue
        day_gaps = [
            (dates[idx] - dates[idx - 1]).days
            for idx in range(1, len(dates))
            if (dates[idx] - dates[idx - 1]).days > 0
        ]
        monthly_gap_cnt = sum(1 for g in day_gaps if 24 <= g <= 38)
        if monthly_gap_cnt <= 0:
            continue

        amount_bucket = int(round(avg_amount / 1000.0) * 1000)
        score = 0.52
        score += min(0.2, 0.04 * max(0, len(amounts) - min_samples))
        score += min(0.18, 0.06 * max(0, len(month_keys) - 1))
        score += min(0.1, 0.05 * monthly_gap_cnt)
        confidence = max(0.0, min(0.95, round(score, 3)))
        cp_display = max((v[0] for v in items if v[0]), key=len, default=key)

        candidates.append(
            RecurringCandidate(
                user_pk=user_pk,
                direction=direction,
                counterparty=cp_display,
                amount_bucket=max(0, int(amount_bucket)),
                cadence="monthly",
                confidence=float(confidence),
                sample_count=int(len(amounts)),
                last_seen_at=max(dates),
            )
        )

    (
        RecurringCandidate.query.filter(RecurringCandidate.user_pk == user_pk)
        .delete(synchronize_session=False)
    )
    if candidates:
        db.session.bulk_save_objects(candidates)
    db.session.commit()
    return len(candidates)


def list_recurring_candidates(user_pk: int, *, limit: int = 5) -> list[dict]:
    rows = (
        RecurringCandidate.query
        .filter(RecurringCandidate.user_pk == user_pk)
        .order_by(RecurringCandidate.confidence.desc(), RecurringCandidate.last_seen_at.desc())
        .limit(max(1, int(limit or 5)))
        .all()
    )
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "id": int(row.id),
                "direction": row.direction,
                "counterparty": row.counterparty or "알 수 없음",
                "amount_bucket": int(row.amount_bucket or 0),
                "confidence_pct": int(round(float(row.confidence or 0) * 100)),
                "sample_count": int(row.sample_count or 0),
                "last_seen_at": row.last_seen_at,
            }
        )
    return out


def detect_large_transaction_outliers(
    user_pk: int,
    *,
    month_key: str | None = None,
    lookback_days: int = 90,
    limit: int = 20,
) -> list[dict]:
    """평균 대비 비정상적으로 큰 거래(수입/지출)를 찾아 반환한다."""
    month_key = month_key or _month_key_now()
    start_dt, end_dt = _month_range_kst_naive(month_key)
    baseline_start = start_dt - timedelta(days=max(30, int(lookback_days or 90)))

    baseline_rows = (
        db.session.query(Transaction.direction, Transaction.amount_krw)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= baseline_start, Transaction.occurred_at < start_dt)
        .filter(Transaction.amount_krw > 0)
        .all()
    )
    if not baseline_rows:
        return []

    amounts_by_dir: dict[str, list[int]] = {"in": [], "out": []}
    for direction, amount_krw in baseline_rows:
        if direction not in ("in", "out"):
            continue
        amounts_by_dir[direction].append(int(amount_krw or 0))

    thresholds: dict[str, int] = {}
    for direction in ("in", "out"):
        vals = sorted(v for v in amounts_by_dir.get(direction, []) if v > 0)
        if not vals:
            continue
        avg_val = sum(vals) / len(vals)
        p99_idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * 0.99))))
        p99_val = vals[p99_idx]
        thresholds[direction] = int(max(p99_val, avg_val * 5))

    if not thresholds:
        return []

    month_rows = (
        db.session.query(
            Transaction.id,
            Transaction.occurred_at,
            Transaction.direction,
            Transaction.amount_krw,
            Transaction.counterparty,
            Transaction.memo,
        )
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .filter(Transaction.amount_krw > 0)
        .order_by(Transaction.amount_krw.desc(), Transaction.occurred_at.desc())
        .all()
    )

    outliers: list[dict] = []
    for tx_id, occurred_at, direction, amount_krw, counterparty, memo in month_rows:
        threshold = int(thresholds.get(direction, 0) or 0)
        amount = int(amount_krw or 0)
        if threshold <= 0 or amount < threshold:
            continue
        outliers.append(
            {
                "tx_id": int(tx_id),
                "occurred_at": occurred_at,
                "direction": direction,
                "amount_krw": amount,
                "counterparty": (counterparty or memo or "알 수 없음"),
                "reason": f"최근 평균 대비 큰 거래(기준 {threshold:,}원 이상)",
            }
        )
        if len(outliers) >= max(1, int(limit or 20)):
            break
    return outliers


def build_industry_missing_cost_hints(
    user_pk: int,
    *,
    month_key: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """업종별 자주 발생하는 비용 카테고리 누락 힌트를 제공한다."""
    month_key = month_key or _month_key_now()
    profile = get_tax_profile(user_pk)
    industry = str(profile.get("industry_group") or "unknown")
    if industry == "unknown":
        return []

    hint_rules: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        "it": [
            ("소프트웨어/클라우드", ("aws", "gcp", "azure", "소프트웨어", "구독", "도메인")),
            ("통신/인터넷", ("통신", "휴대폰", "인터넷", "요금")),
            ("장비/주변기기", ("노트북", "모니터", "장비", "키보드", "마우스")),
        ],
        "design": [
            ("디자인 툴/구독", ("adobe", "figma", "canva", "구독", "툴")),
            ("장비/소모품", ("태블릿", "펜", "장비", "소모품", "프린트")),
        ],
        "marketing": [
            ("광고/홍보비", ("광고", "meta", "google ads", "네이버", "홍보")),
            ("도구/분석툴", ("analytics", "crm", "툴", "구독")),
        ],
        "creator": [
            ("촬영/편집 툴", ("촬영", "편집", "adobe", "캡컷", "구독")),
            ("장비/소모품", ("카메라", "마이크", "조명", "장비")),
        ],
        "consulting": [
            ("교통/출장", ("택시", "교통", "출장", "주유", "통행료")),
            ("회의/협업 도구", ("zoom", "미트", "회의", "구독")),
        ],
    }
    rules = hint_rules.get(industry)
    if not rules:
        return []

    start_dt, end_dt = _month_range_kst_naive(month_key)
    out_rows = (
        db.session.query(Transaction.counterparty, Transaction.memo)
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .all()
    )
    texts = [
        f"{(cp or '').lower()} {(memo or '').lower()}".strip()
        for cp, memo in out_rows
    ]

    hints: list[dict] = []
    for title, keywords in rules:
        matched = any(any(k in text for k in keywords) for text in texts)
        if matched:
            continue
        q = keywords[0]
        hints.append(
            {
                "title": f"{title} 누락 가능성",
                "message": f"{title} 관련 비용이 이번 달에 보이지 않아요. 빠진 내역이 없는지 확인해 보세요.",
                "search_q": q,
            }
        )
        if len(hints) >= max(1, int(limit or 3)):
            break
    return hints


def compute_overview(user_pk: int, month_key: str | None = None) -> dict:
    r = compute_risk_summary(
        user_pk,
        month_key=month_key,
        prefer_monthly_signal=True,
    )

    month_key_val = r.month_key
    tax_est = compute_tax_estimate(
        user_pk=user_pk,
        month_key=month_key_val,
        prefer_monthly_signal=True,
    )
    tax_result_meta = build_tax_result_meta(tax_est)
    tax_setaside = int(r.buffer_target_krw)
    tax_buffer = int(r.buffer_total_krw)
    tax_shortfall = int(r.buffer_shortage_krw)
    tax_progress_percent = 100.0 if tax_setaside <= 0 else min(100.0, (tax_buffer / tax_setaside) * 100.0)

    profile = get_tax_profile(user_pk)
    profile_complete = bool(is_tax_profile_complete(profile))
    health_insurance_buffer, _health_insurance_note, _nhis_payload = compute_nhis_monthly_buffer(
        user_pk=user_pk,
        month_key=month_key_val,
    )
    nhis_result_meta = dict((_nhis_payload or {}).get("result_meta") or {})
    official_data_effect_notice = summarize_official_data_effects(
        tax_estimate=tax_est,
        nhis_result_meta=nhis_result_meta,
    )
    tax_accuracy_level = str(tax_result_meta.get("accuracy_level") or "limited").strip().lower()
    nhis_accuracy_level = str(nhis_result_meta.get("accuracy_level") or "limited").strip().lower()
    tax_display_policy = {
        "accuracy_level": tax_accuracy_level,
        "blocked": tax_accuracy_level == "blocked",
        "limited": tax_accuracy_level == "limited",
        "strong": tax_accuracy_level in {"exact_ready", "high_confidence"},
    }
    nhis_display_policy = {
        "accuracy_level": nhis_accuracy_level,
        "blocked": nhis_accuracy_level == "blocked",
        "limited": nhis_accuracy_level == "limited",
        "strong": nhis_accuracy_level in {"exact_ready", "high_confidence"},
    }
    core_numbers_blocked = bool(tax_display_policy["blocked"] or nhis_display_policy["blocked"])
    total_setaside = int(tax_setaside + int(health_insurance_buffer))

    required_missing = int(r.evidence_missing_required)
    review_needed = int(r.expense_needs_review + r.income_unknown)

    import_url = "/inbox/import"
    review_required_url = f"/dashboard/review?month={month_key_val}&lane=required&focus=receipt_required"
    if r.expense_needs_review > 0:
        review_classify_url = f"/dashboard/review?month={month_key_val}&lane=review&focus=expense_confirm"
    else:
        review_classify_url = f"/dashboard/review?month={month_key_val}&lane=review&focus=income_confirm"
    tax_buffer_url = f"/dashboard/tax-buffer?month={month_key_val}"
    package_url = f"/dashboard/package?month={month_key_val}"
    profile_url = (
        f"/dashboard/profile?step=2&next=/overview&return_to_next=1"
        "&recovery_source=overview_accuracy_card"
    )
    tax_recovery_url = "/dashboard/profile?step=2&recovery_source=tax_overview_cta"
    if str(tax_result_meta.get("reason") or "") == TAX_REASON_MISSING_INCOME_CLASSIFICATION:
        tax_recovery_url = "/dashboard/profile?step=2&focus=income_classification&recovery_source=tax_overview_cta"
    nhis_recovery_url = f"/dashboard/nhis?month={month_key_val}&recovery_source=nhis_overview_cta#asset-diagnosis"
    has_transactions = bool(int(r.gross_income_krw or 0) > 0 or int(r.expenses_krw or 0) > 0)

    package_status = "warn"
    package_badge = "보완 필요"
    package_hint = "다운로드 전 점검에서 남은 항목을 확인해 주세요."
    package_ready_percent = 0
    package_top_issues: list[dict] = []
    try:
        preview = build_tax_package_preview(user_pk=user_pk, month_key=month_key_val)
        pf = (preview or {}).get("preflight") or {}
        package_status = str(pf.get("status") or "warn")
        package_ready_percent = int(round(float(pf.get("required_attachment_rate_pct") or 0)))
        fail_count = int(pf.get("fail_count") or 0)
        warn_count = int(pf.get("warn_count") or 0)
        if package_status == "pass":
            package_badge = "통과"
            package_hint = "지금 내려받아 세무사에게 전달할 수 있어요."
        elif package_status == "warn":
            package_badge = "보완 권장"
            package_hint = f"권장 보완 {warn_count}건이 남아 있어요."
        else:
            package_badge = "보완 필요"
            package_hint = f"필수 보완 {fail_count}건을 먼저 처리해 주세요."
        package_top_issues = list((pf.get("top_issues") or [])[:3])
    except Exception:
        package_status = "warn"
        package_badge = "확인 필요"
        package_hint = "패키지 화면에서 다운로드 전 점검을 확인해 주세요."
        package_top_issues = []

    today_tasks: list[dict] = []
    if not has_transactions:
        today_tasks.append(
            {
                "title": "파일 업로드로 시작하기",
                "desc": "내역을 가져오면 세금/증빙/패키지 점검이 바로 시작됩니다.",
                "action_text": "가져오기",
                "url": import_url,
                "level": "primary",
            }
        )
    if has_transactions and required_missing > 0:
        today_tasks.append(
            {
                "title": f"영수증 첨부 {required_missing}건",
                "desc": "필수 누락부터 처리하면 전체 정리가 빨라져요.",
                "action_text": "바로 처리",
                "url": review_required_url,
                "level": "bad",
            }
        )
    if has_transactions and review_needed > 0:
        today_tasks.append(
            {
                "title": f"분류가 필요한 거래 {review_needed}건",
                "desc": "업무/개인만 확정해도 숫자가 바로 정확해져요.",
                "action_text": "지금 분류",
                "url": review_classify_url,
                "level": "warn",
            }
        )
    if has_transactions and (not profile_complete):
        today_tasks.append(
            {
                "title": "기본 정보 1분 입력",
                "desc": "입력하면 세금/증빙 정확도가 올라가요.",
                "action_text": "내 정보 입력",
                "url": profile_url,
                "level": "primary",
            }
        )
    if not today_tasks:
        today_tasks.append(
            {
                "title": "이번 달은 정리 완료에 가까워요",
                "desc": "다운로드 전 점검만 확인하고 전달하면 됩니다.",
                "action_text": "패키지 확인",
                "url": package_url,
                "level": "good",
            }
        )
    today_tasks = today_tasks[:3]
    next_action_url = today_tasks[0]["url"] if today_tasks else f"/dashboard/review?month={month_key_val}"
    improvement_cards: list[dict] = []
    if not has_transactions:
        improvement_cards.append(
            {
                "kind": "quick_start",
                "title": "결과를 보려면 먼저 내역만 가져오면 돼요",
                "desc": "계좌나 파일을 가져오면 이번 달 숫자와 해야 할 일을 바로 보여드려요.",
                "impact": "첫 결과 화면을 만드는 가장 빠른 방법이에요.",
                "action_text": "내역 가져오기",
                "url": import_url,
            }
        )
    else:
        if not profile_complete:
            improvement_cards.append(
                {
                    "kind": "tax_accuracy",
                    "title": "이 정보 1개만 더 있으면 예상세금이 더 정확해져요",
                    "desc": "돈 받을 때 3.3%가 떼이는지와 올해 들어온 돈, 일하면서 쓴 비용 정도만 알려주면 돼요.",
                    "impact": "세금 보관 권장액과 분류 정확도가 더 또렷해져요.",
                    "action_text": "기본 정보 이어서 입력",
                    "url": profile_url,
                }
            )
        if required_missing > 0:
            improvement_cards.append(
                {
                    "kind": "receipt_reflection",
                    "title": "영수증만 붙이면 비용 반영 준비가 끝나는 거래가 있어요",
                    "desc": f"이번 달에 영수증이 꼭 필요한 거래 {required_missing}건이 남아 있어요.",
                    "impact": "붙여 두면 비용 반영 가능성과 세무사 전달 품질이 함께 올라가요.",
                    "action_text": "영수증부터 정리",
                    "url": review_required_url,
                }
            )
        elif review_needed > 0:
            improvement_cards.append(
                {
                    "kind": "receipt_reflection",
                    "title": "이 답변만 끝내면 비용 반영 가능성이 올라가요",
                    "desc": f"업무인지 개인인지 아직 정하지 않은 거래 {review_needed}건이 있어요.",
                    "impact": "업무로 확정되면 숫자에 실제로 반영될 수 있어요.",
                    "action_text": "거래 답변 이어서 하기",
                    "url": review_classify_url,
                }
            )
        if package_status != "pass":
            package_action_url = (
                str(package_top_issues[0].get("action_url") or package_url)
                if package_top_issues
                else package_url
            )
            improvement_cards.append(
                {
                    "kind": "package_quality",
                    "title": "세무사에게 보낼 자료를 더 분명하게 만들 수 있어요",
                    "desc": package_hint,
                    "impact": "메모와 증빙이 정리되면 전달 자료에서 다시 설명할 일이 줄어들어요.",
                    "action_text": "전달 자료 확인",
                    "url": package_action_url,
                }
            )
    if not improvement_cards:
        improvement_cards.append(
            {
                "kind": "all_set",
                "title": "지금은 결과가 잘 정리돼 있어요",
                "desc": "숫자를 확인하고 필요하면 패키지만 한 번 더 점검하면 됩니다.",
                "impact": "입력을 더 강하게 요구하지 않아요.",
                "action_text": "정리 화면 보기",
                "url": next_action_url,
            }
        )
    improvement_cards = improvement_cards[:3]
    recurring_candidates = list_recurring_candidates(user_pk=user_pk, limit=5)
    now_month_key = _month_key_now()
    show_month_end_banner = False
    month_end_days_left: int | None = None
    if has_transactions and (month_key_val == now_month_key):
        now_kst_date = datetime.now(timezone.utc).astimezone(KST).date()
        _mk_start, mk_end = _month_range_kst_naive(month_key_val)
        month_last = (mk_end - timedelta(days=1)).date()
        month_end_days_left = int((month_last - now_kst_date).days)
        show_month_end_banner = bool(0 <= month_end_days_left <= 3)

    tax_recovery_cta = build_tax_recovery_cta(
        tax_result_meta,
        recovery_url=tax_recovery_url,
    )
    nhis_recovery_cta = build_nhis_recovery_cta(
        nhis_result_meta,
        recovery_url=nhis_recovery_url,
    )

    settings = _get_settings(user_pk)
    return dict(
        month_key=month_key_val,
        gross_income=int(r.gross_income_krw),
        expenses=int(r.expenses_krw),
        tax_target_rate=float(getattr(settings, "default_tax_rate", 0.15) or 0.15),
        tax_target=int(tax_setaside),
        tax_buffer=int(tax_buffer),
        tax_shortfall=int(tax_shortfall),
        tax_progress_percent=float(tax_progress_percent),
        evidence_missing_count=int(r.evidence_missing_required + r.evidence_missing_maybe),
        mixed_count=int(r.expense_needs_review),
        income_unknown_count=int(r.income_unknown),
        required_missing_count=int(required_missing),
        review_needed_count=int(review_needed),
        tax_setaside_recommended=int(tax_setaside),
        health_insurance_buffer=int(health_insurance_buffer),
        total_setaside_recommended=int(total_setaside),
        tax_result_meta=tax_result_meta,
        nhis_result_meta=nhis_result_meta,
        official_data_effect_notice=official_data_effect_notice,
        tax_display_policy=tax_display_policy,
        nhis_display_policy=nhis_display_policy,
        core_numbers_blocked=core_numbers_blocked,
        tax_recovery_cta=tax_recovery_cta,
        nhis_recovery_cta=nhis_recovery_cta,
        has_recovery_cta=bool(tax_recovery_cta.get("show") or nhis_recovery_cta.get("show")),
        profile_complete=bool(profile_complete),
        profile_badge_text=("내 정보 반영됨" if profile_complete else "기본값으로 추정"),
        package_status=package_status,
        package_badge=package_badge,
        package_hint=package_hint,
        package_ready_percent=int(max(0, min(100, package_ready_percent))),
        package_top_issues=package_top_issues,
        tax_buffer_url=tax_buffer_url,
        review_required_url=review_required_url,
        review_classify_url=review_classify_url,
        package_url=package_url,
        import_url=import_url,
        has_transactions=has_transactions,
        today_tasks=today_tasks,
        recurring_candidates=recurring_candidates,
        show_month_end_banner=show_month_end_banner,
        month_end_days_left=month_end_days_left,
        next_action_url=next_action_url,
        improvement_cards=improvement_cards,
    )


def compute_inbox_counts(user_pk: int) -> dict:
    evidence = (
        db.session.query(func.count(EvidenceItem.id))
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement.in_(("required", "maybe")))
        .scalar()
    ) or 0

    mixed = (
        db.session.query(func.count(ExpenseLabel.id))
        .filter(ExpenseLabel.user_pk == user_pk)
        .filter(ExpenseLabel.status.in_(("mixed", "unknown")))
        .scalar()
    ) or 0

    income = (
        db.session.query(func.count(IncomeLabel.id))
        .filter(IncomeLabel.user_pk == user_pk)
        .filter(IncomeLabel.status == "unknown")
        .scalar()
    ) or 0

    return {"evidence": int(evidence), "mixed": int(mixed), "income": int(income)}


def compute_inbox(user_pk: int, tab: str, limit: int = 60) -> list[dict]:
    """
    inbox 탭별 목록 데이터. (전체 미처리 기준)
    - evidence: EvidenceItem + Transaction
    - mixed: ExpenseLabel + Transaction
    - income: IncomeLabel + Transaction
    """
    items: list[dict] = []

    if tab == "evidence":
        q = (
            db.session.query(EvidenceItem, Transaction)
            .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
            .filter(EvidenceItem.user_pk == user_pk)
            .filter(EvidenceItem.status == "missing")
            .filter(EvidenceItem.requirement.in_(("required", "maybe")))
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
        for e, tx in q:
            level = "bad" if e.requirement == "required" else "warn"
            badge = "증빙 필수" if e.requirement == "required" else "증빙 필요(확인)"
            reason = "증빙이 없으면 비용처리/소명에서 리스크가 커집니다. 지금 1건만 처리하세요."
            items.append(
                dict(
                    kind="evidence",
                    level=level,
                    badge=badge,
                    reason=reason,
                    evidence_id=e.id,
                    date=_as_kst_date_str(tx.occurred_at),
                    counterparty=tx.counterparty,
                    amount=int(tx.amount_krw),
                    direction=tx.direction,
                )
            )

    elif tab == "mixed":
        q = (
            db.session.query(ExpenseLabel, Transaction)
            .join(Transaction, Transaction.id == ExpenseLabel.transaction_id)
            .filter(ExpenseLabel.user_pk == user_pk)
            .filter(ExpenseLabel.status.in_(("mixed", "unknown")))
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
        for lab, tx in q:
            items.append(
                dict(
                    kind="mixed",
                    level="warn",
                    badge="혼재 의심",
                    reason="사업/개인이 섞이면 정리도, 세무도 계속 꼬입니다. 한 번만 확정하세요.",
                    label_id=lab.id,
                    date=_as_kst_date_str(tx.occurred_at),
                    counterparty=tx.counterparty,
                    amount=int(tx.amount_krw),
                    direction=tx.direction,
                )
            )

    else:  # income
        q = (
            db.session.query(IncomeLabel, Transaction)
            .join(Transaction, Transaction.id == IncomeLabel.transaction_id)
            .filter(IncomeLabel.user_pk == user_pk)
            .filter(IncomeLabel.status == "unknown")
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
        for lab, tx in q:
            items.append(
                dict(
                    kind="income",
                    level="info",
                    badge="수입 의심",
                    reason="수입/수입아님만 확정해도 세금 금고 목표가 더 정확해집니다.",
                    label_id=lab.id,
                    date=_as_kst_date_str(tx.occurred_at),
                    counterparty=tx.counterparty,
                    amount=int(tx.amount_krw),
                    direction=tx.direction,
                )
            )

    return items
