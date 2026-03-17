# services/risk.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from core.extensions import db
from services.official_data_effects import collect_official_tax_effects_for_user_month
from domain.models import (
    Transaction,
    EvidenceItem,
    ExpenseLabel,
    IncomeLabel,
    TaxBufferLedger,
    SafeToSpendSettings,
)

KST = ZoneInfo("Asia/Seoul")


def _month_key_now() -> str:
    """현재 시각 기준(한국시간) YYYY-MM"""
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    return now_kst.strftime("%Y-%m")


def _month_range_utc(month_key: str) -> tuple[datetime, datetime]:
    """
    month_key(YYYY-MM)의 '한국시간 월 경계'를 잡고,
    DB 비교용으로 UTC datetime(aware) 범위를 반환.
    """
    y, m = month_key.split("-")
    y = int(y)
    m = int(m)

    start_kst = datetime(y, m, 1, 0, 0, 0, tzinfo=KST)
    if m == 12:
        end_kst = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=KST)
    else:
        end_kst = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=KST)

    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


def _get_settings(user_pk: int) -> SafeToSpendSettings:
    """
    settings 테이블(= SafeToSpendSettings 매핑)에 사용자 기본 세율이 없으면 생성.
    """
    s = SafeToSpendSettings.query.get(user_pk)
    if not s:
        s = SafeToSpendSettings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
        db.session.add(s)
        db.session.commit()
    return s


def _as_kst_date_str(dt: datetime) -> str:
    """
    Transaction.occurred_at가 naive(타임존 없음)일 수 있음.
    이 프로젝트 import_csv는 KST로 파싱 후 UTC로 변환해 저장하므로,
    naive면 'UTC naive'로 간주해 KST로 표시한다.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d")


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
    tax_due_before_official_data_krw: int
    tax_due_after_official_data_krw: int
    official_withheld_tax_krw: int
    official_paid_tax_krw: int
    tax_delta_from_official_data_krw: int
    official_tax_reference_date: str | None
    official_tax_effect_status: str
    official_tax_effect_strength: str
    official_tax_effect_reason: str
    official_tax_effect_source_count: int
    official_tax_effect_document_types: tuple[str, ...]
    official_tax_confidence_label: str
    official_tax_verification_badge: str
    official_tax_verification_hint: str
    official_tax_verification_level: str
    official_tax_is_high_confidence: bool


def compute_risk_summary(user_pk: int, month_key: str | None = None) -> RiskSummary:
    """
    - 월간(한국시간 기준) 매출/지출
    - (✅ 월 범위 적용) 증빙 누락/라벨 미확정 건수
    - 세금 금고(ledger) 현황과 목표 대비 부족액
    """
    month_key = month_key or _month_key_now()
    start_utc, end_utc = _month_range_utc(month_key)

    settings = _get_settings(user_pk)
    tax_rate = float(settings.default_tax_rate or 0.15)
    if tax_rate > 1:
        tax_rate = tax_rate / 100.0
    tax_rate = max(0.0, min(tax_rate, 0.95))

    gross_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "in")
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    expenses = (
        db.session.query(func.coalesce(func.sum(Transaction.amount_krw), 0))
        .filter(Transaction.user_pk == user_pk)
        .filter(Transaction.direction == "out")
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    # ✅ 증빙 누락(필수/확인) - 월 범위 적용 (Transaction join)
    evidence_required = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement == "required")
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    evidence_maybe = (
        db.session.query(func.count(EvidenceItem.id))
        .join(Transaction, Transaction.id == EvidenceItem.transaction_id)
        .filter(EvidenceItem.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(EvidenceItem.status == "missing")
        .filter(EvidenceItem.requirement == "maybe")
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    # ✅ 비용 라벨 미확정/혼재 - 월 범위 적용
    expense_review = (
        db.session.query(func.count(ExpenseLabel.id))
        .join(Transaction, Transaction.id == ExpenseLabel.transaction_id)
        .filter(ExpenseLabel.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(ExpenseLabel.status.in_(("mixed", "unknown")))
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    # ✅ 수입 라벨 미확정 - 월 범위 적용
    income_unknown = (
        db.session.query(func.count(IncomeLabel.id))
        .join(Transaction, Transaction.id == IncomeLabel.transaction_id)
        .filter(IncomeLabel.user_pk == user_pk)
        .filter(Transaction.user_pk == user_pk)
        .filter(IncomeLabel.status == "unknown")
        .filter(Transaction.occurred_at >= start_utc, Transaction.occurred_at < end_utc)
        .scalar()
    ) or 0

    # 세금 금고는 누적(전체 합)로 유지
    buffer_total = (
        db.session.query(func.coalesce(func.sum(TaxBufferLedger.delta_amount_krw), 0))
        .filter(TaxBufferLedger.user_pk == user_pk)
        .scalar()
    ) or 0

    buffer_target_before = int(gross_income * tax_rate)
    official_tax_effect = collect_official_tax_effects_for_user_month(int(user_pk), month_key=month_key)
    official_withheld_tax = int(official_tax_effect.get("official_withheld_tax_krw") or 0)
    official_paid_tax = int(official_tax_effect.get("official_paid_tax_krw") or 0)
    official_adjustment = max(0, official_withheld_tax + official_paid_tax)
    buffer_target_after = max(0, buffer_target_before - official_adjustment)
    buffer_shortage = max(0, buffer_target_after - int(buffer_total))
    tax_delta = int(buffer_target_after - buffer_target_before)

    return RiskSummary(
        month_key=month_key,
        gross_income_krw=int(gross_income),
        expenses_krw=int(expenses),
        evidence_missing_required=int(evidence_required),
        evidence_missing_maybe=int(evidence_maybe),
        expense_needs_review=int(expense_review),
        income_unknown=int(income_unknown),
        buffer_total_krw=int(buffer_total),
        buffer_target_krw=int(buffer_target_after),
        buffer_shortage_krw=int(buffer_shortage),
        tax_due_before_official_data_krw=int(buffer_target_before),
        tax_due_after_official_data_krw=int(buffer_target_after),
        official_withheld_tax_krw=int(official_withheld_tax),
        official_paid_tax_krw=int(official_paid_tax),
        tax_delta_from_official_data_krw=int(tax_delta),
        official_tax_reference_date=official_tax_effect.get("official_tax_reference_date"),
        official_tax_effect_status=str(official_tax_effect.get("official_tax_effect_status") or "none"),
        official_tax_effect_strength=str(official_tax_effect.get("official_tax_effect_strength") or "none"),
        official_tax_effect_reason=str(official_tax_effect.get("official_tax_effect_reason") or ""),
        official_tax_effect_source_count=int(official_tax_effect.get("official_tax_effect_source_count") or 0),
        official_tax_effect_document_types=tuple(official_tax_effect.get("official_tax_effect_document_types") or ()),
        official_tax_confidence_label=str(official_tax_effect.get("official_tax_confidence_label") or ""),
        official_tax_verification_badge=str(official_tax_effect.get("official_tax_verification_badge") or ""),
        official_tax_verification_hint=str(official_tax_effect.get("official_tax_verification_hint") or ""),
        official_tax_verification_level=str(official_tax_effect.get("official_tax_verification_level") or "none"),
        official_tax_is_high_confidence=bool(official_tax_effect.get("official_tax_is_high_confidence")),
    )


def compute_overview(user_pk: int, month_key: str | None = None) -> dict:
    """
    overview 페이지에 필요한 '월간 요약 + 이번 주 할 일 1개' 컨텍스트 생성.
    """
    r = compute_risk_summary(user_pk, month_key=month_key)

    tax_target = r.buffer_target_krw
    tax_buffer = r.buffer_total_krw
    tax_shortfall = r.buffer_shortage_krw

    tax_progress_percent = 100.0 if tax_target <= 0 else min(100.0, (tax_buffer / tax_target) * 100.0)

    # “이번 주 할 일 1개”: 가장 강한 체감부터
    evidence_missing_count = r.evidence_missing_required + r.evidence_missing_maybe
    mixed_count = r.expense_needs_review

    if evidence_missing_count > 0:
        weekly_task_title = f"증빙 누락 {evidence_missing_count}건 중 1건만 처리"
        weekly_task_desc = "증빙 1건만 붙여도 리스크가 확 줄고, 다음 주가 편해집니다."
        weekly_task_cta = "증빙 1건 처리하기"
        weekly_task_url = "/inbox?tab=evidence"
        weekly_task_level = "bad"
        weekly_task_badge = "긴급"
    elif mixed_count > 0:
        weekly_task_title = f"혼재/미확정 {mixed_count}건 중 1건만 확정"
        weekly_task_desc = "사업/개인만 확정해도 대시보드 정확도가 확 올라갑니다."
        weekly_task_cta = "1건 확정하기"
        weekly_task_url = "/inbox?tab=mixed"
        weekly_task_level = "warn"
        weekly_task_badge = "중요"
    elif tax_shortfall > 0:
        weekly_task_title = "세금 금고 부족액 확인하기"
        weekly_task_desc = "부족액을 인지하는 순간부터 세금 폭탄 확률이 떨어집니다."
        weekly_task_cta = "요약에서 확인"
        weekly_task_url = "/overview"
        weekly_task_level = "warn"
        weekly_task_badge = "중요"
    else:
        weekly_task_title = "좋아요. 이번 주는 유지 모드"
        weekly_task_desc = "업로드/연동만 유지하면 문제는 자동으로 다시 올라옵니다."
        weekly_task_cta = "처리함 확인"
        weekly_task_url = "/inbox"
        weekly_task_level = "info"
        weekly_task_badge = "안정"

    return dict(
        month_key=r.month_key,
        gross_income=int(r.gross_income_krw),
        expenses=int(r.expenses_krw),

        tax_target_rate=float(_get_settings(user_pk).default_tax_rate or 0.15),
        tax_target=int(tax_target),
        tax_buffer=int(tax_buffer),
        tax_shortfall=int(tax_shortfall),
        tax_progress_percent=float(tax_progress_percent),
        tax_due_before_official_data_krw=int(r.tax_due_before_official_data_krw),
        tax_due_after_official_data_krw=int(r.tax_due_after_official_data_krw),
        official_withheld_tax_krw=int(r.official_withheld_tax_krw),
        official_paid_tax_krw=int(r.official_paid_tax_krw),
        tax_delta_from_official_data_krw=int(r.tax_delta_from_official_data_krw),
        official_tax_reference_date=r.official_tax_reference_date,
        official_tax_effect_status=r.official_tax_effect_status,
        official_tax_effect_strength=r.official_tax_effect_strength,
        official_tax_effect_reason=r.official_tax_effect_reason,
        official_tax_effect_source_count=int(r.official_tax_effect_source_count),
        official_tax_effect_document_types=tuple(r.official_tax_effect_document_types),
        official_tax_confidence_label=r.official_tax_confidence_label,
        official_tax_verification_badge=r.official_tax_verification_badge,
        official_tax_verification_hint=r.official_tax_verification_hint,
        official_tax_verification_level=r.official_tax_verification_level,
        official_tax_is_high_confidence=bool(r.official_tax_is_high_confidence),

        evidence_missing_count=int(evidence_missing_count),
        mixed_count=int(mixed_count),

        weekly_task_title=weekly_task_title,
        weekly_task_desc=weekly_task_desc,
        weekly_task_cta=weekly_task_cta,
        weekly_task_url=weekly_task_url,
        weekly_task_level=weekly_task_level,
        weekly_task_badge=weekly_task_badge,
    )


def compute_inbox_counts(user_pk: int) -> dict:
    """inbox 상단 탭 배지 숫자. (전체 미처리 기준)"""
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
