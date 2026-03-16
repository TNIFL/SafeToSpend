#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from core.extensions import db
from core.time import utcnow
from domain.models import (  # noqa: E402
    EvidenceItem,
    ExpenseLabel,
    ImportJob,
    IncomeLabel,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    Settings,
    TaxProfile,
    Transaction,
    User,
)
from services.auth import register_user  # noqa: E402
from services.onboarding import save_onboarding, save_tax_profile  # noqa: E402
from services.receipt_expense_rules import evaluate_receipt_expense_with_follow_up  # noqa: E402
from services.risk import compute_tax_estimate  # noqa: E402

SEED_EMAIL = "e2e+receipt-tax-effects@safetospend.local"
SEED_PASSWORD = "Test1234!"
MONTH_KEY = "2026-03"
SUMMARY_PATH = ROOT / "reports" / "receipt_tax_effects_e2e_summary.json"
FAILURES_PATH = ROOT / "reports" / "receipt_tax_effects_e2e_failures.json"


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    occurred_at: datetime
    amount_krw: int
    counterparty: str
    memo: str
    expectation: str


CASE_SPECS = (
    CaseSpec(
        case_id="followup_reflect_transport",
        occurred_at=datetime(2026, 3, 14, 23, 30),
        amount_krw=48_000,
        counterparty="서울택시",
        memo="야간 이동",
        expectation="weekend transport; follow-up reason should promote to high_likelihood",
    ),
    CaseSpec(
        case_id="reinforcement_reflect_meal",
        occurred_at=datetime(2026, 3, 12, 14, 30),
        amount_krw=18_000,
        counterparty="스타벅스",
        memo="거래처 미팅 커피",
        expectation="business meal candidate; follow-up alone stays needs_review, reinforcement can promote",
    ),
    CaseSpec(
        case_id="pending_cafe_no_change",
        occurred_at=datetime(2026, 3, 13, 11, 40),
        amount_krw=13_000,
        counterparty="스타벅스",
        memo="회의 전 커피",
        expectation="cafe spend remains pending after follow-up",
    ),
    CaseSpec(
        case_id="consult_asset",
        occurred_at=datetime(2026, 3, 11, 15, 20),
        amount_krw=2_400_000,
        counterparty="애플스토어",
        memo="맥북 프로",
        expectation="high-value asset remains consult_tax_review",
    ),
    CaseSpec(
        case_id="reduced_motion_transport",
        occurred_at=datetime(2026, 3, 15, 0, 15),
        amount_krw=52_000,
        counterparty="서울택시",
        memo="새벽 공항 이동",
        expectation="reduced motion follow-up case should promote to high_likelihood",
    ),
)


def _ensure_user() -> User:
    user = User.query.filter_by(email=SEED_EMAIL).first()
    if not user:
        ok, msg = register_user(SEED_EMAIL, SEED_PASSWORD)
        if not ok and "이미 가입된 이메일" not in str(msg):
            raise RuntimeError(f"E2E 계정 생성 실패: {msg}")
        user = User.query.filter_by(email=SEED_EMAIL).first()
    if not user:
        raise RuntimeError("E2E 계정을 조회하지 못했습니다.")
    user.set_password(SEED_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _reset_user_data(user_pk: int) -> None:
    ReceiptExpenseFollowupAnswer.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    ReceiptExpenseReinforcement.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    IncomeLabel.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    ExpenseLabel.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    EvidenceItem.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    Transaction.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    ImportJob.query.filter_by(user_pk=user_pk).delete(synchronize_session=False)
    db.session.commit()


def _ensure_user_defaults(user_pk: int) -> None:
    settings = Settings.query.filter_by(user_pk=user_pk).first()
    if not settings:
        settings = Settings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
    settings.default_tax_rate = 0.15
    settings.nhi_monthly_krw = 120_000
    meta = dict(settings.custom_rates or {})
    meta["_meta"] = {
        "onboarding_done": True,
        "freelancer_type": "developer",
        "monthly_income_band": "3m_6m",
        "work_mode": "solo",
        "primary_goal": "tax_ready",
        "completed_at": utcnow().isoformat(timespec="minutes"),
    }
    settings.custom_rates = meta
    db.session.add(settings)
    db.session.commit()

    ok, msg = save_onboarding(
        user_pk=user_pk,
        freelancer_type="developer",
        monthly_income_band="3m_6m",
        work_mode="solo",
        primary_goal="tax_ready",
    )
    if not ok:
        raise RuntimeError(f"온보딩 기본값 저장 실패: {msg}")

    ok, msg = save_tax_profile(
        user_pk=user_pk,
        payload={
            "industry_group": "it",
            "industry_text": "",
            "tax_type": "general",
            "prev_income_band": "30m_80m",
            "withholding_3_3": "no",
            "other_income": "no",
            "other_income_types": [],
            "health_insurance_type": "regional",
            "health_insurance_monthly_krw": 120_000,
            "income_classification": "business",
            "annual_gross_income_krw": 72_000_000,
            "annual_deductible_expense_krw": 12_000_000,
            "withheld_tax_annual_krw": 0,
            "prepaid_tax_annual_krw": 0,
            "tax_basic_inputs_confirmed": True,
            "wizard_last_step": 3,
            "profile_flow_done": True,
        },
    )
    if not ok:
        raise RuntimeError(f"세금 프로필 시드 실패: {msg}")


def _make_tx(user_pk: int, *, case_id: str, occurred_at: datetime, direction: str, amount_krw: int, counterparty: str, memo: str) -> Transaction:
    tx = Transaction(
        user_pk=user_pk,
        import_job_id=None,
        occurred_at=occurred_at,
        direction=direction,
        amount_krw=amount_krw,
        counterparty=counterparty,
        memo=memo,
        source="e2e",
        review_state="todo",
        external_hash=f"receipt-tax-effects-e2e:{case_id}",
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _seed_income(user_pk: int) -> Transaction:
    tx = _make_tx(
        user_pk,
        case_id="base_income",
        occurred_at=datetime(2026, 3, 3, 9, 0),
        direction="in",
        amount_krw=6_000_000,
        counterparty="E2E 프로젝트 수입",
        memo="월간 용역 대금",
    )
    db.session.add(
        IncomeLabel(
            user_pk=user_pk,
            transaction_id=tx.id,
            status="income",
            confidence=100,
            labeled_by="user",
            rule_version=1,
            decided_at=utcnow(),
            note="E2E seed",
        )
    )
    db.session.commit()
    return tx


def _seed_expense_case(user_pk: int, spec: CaseSpec) -> Transaction:
    tx = _make_tx(
        user_pk,
        case_id=spec.case_id,
        occurred_at=spec.occurred_at,
        direction="out",
        amount_krw=spec.amount_krw,
        counterparty=spec.counterparty,
        memo=spec.memo,
    )
    evidence = EvidenceItem(
        user_pk=user_pk,
        transaction_id=tx.id,
        requirement="maybe",
        status="attached",
        note=f"E2E evidence placeholder: {spec.case_id}",
        original_filename=f"{spec.case_id}.txt",
        mime_type="text/plain",
        size_bytes=0,
        uploaded_at=utcnow(),
    )
    db.session.add(evidence)
    db.session.commit()
    return tx


def _decision_for(tx: Transaction, *, follow_up_answers=None, reinforcement_data=None):
    return evaluate_receipt_expense_with_follow_up(
        tx=tx,
        focus_kind="expense_confirm",
        follow_up_answers=follow_up_answers,
        reinforcement_data=reinforcement_data,
    )


def main() -> int:
    app = create_app()
    with app.app_context():
        user = _ensure_user()
        _reset_user_data(int(user.id))
        _ensure_user_defaults(int(user.id))
        _seed_income(int(user.id))

        tx_map: dict[str, Transaction] = {}
        for spec in CASE_SPECS:
            tx_map[spec.case_id] = _seed_expense_case(int(user.id), spec)

        baseline_est = compute_tax_estimate(int(user.id), month_key=MONTH_KEY)

        followup_transport_after = _decision_for(
            tx_map["followup_reflect_transport"],
            follow_up_answers={
                "weekend_or_late_night_business_reason": {
                    "answer_text": "토요일 출장 이동",
                }
            },
        )
        meal_after_followup = _decision_for(
            tx_map["reinforcement_reflect_meal"],
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 커피",
                }
            },
        )
        meal_after_reinforcement = _decision_for(
            tx_map["reinforcement_reflect_meal"],
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "A사 미팅 커피",
                }
            },
            reinforcement_data={
                "business_context_note": "A사 미팅 중 음료 결제",
                "attendee_names": "A사 김팀장, 박대리",
                "client_or_counterparty_name": "A사",
            },
        )
        pending_after_followup = _decision_for(
            tx_map["pending_cafe_no_change"],
            follow_up_answers={
                "business_meal_with_client": {
                    "answer_value": "yes",
                    "answer_text": "회의 준비 커피",
                }
            },
        )
        reduced_motion_after = _decision_for(
            tx_map["reduced_motion_transport"],
            follow_up_answers={
                "weekend_or_late_night_business_reason": {
                    "answer_text": "일요일 새벽 공항 출장 이동",
                }
            },
        )
        consult_after_followup = _decision_for(
            tx_map["consult_asset"],
            follow_up_answers={
                "asset_vs_consumable": {
                    "answer_value": "asset",
                    "answer_text": "업무용 노트북",
                }
            },
        )

        payload = {
            "generated_at": utcnow().isoformat(timespec="seconds"),
            "month_key": MONTH_KEY,
            "credentials": {
                "email": SEED_EMAIL,
                "password": SEED_PASSWORD,
                "user_pk": int(user.id),
            },
            "paths": {
                "review": f"/dashboard/review?month={MONTH_KEY}&lane=review&focus=expense_confirm&limit=200",
                "tax_buffer": f"/dashboard/tax-buffer?month={MONTH_KEY}",
                "calendar": f"/dashboard/calendar?month={MONTH_KEY}",
            },
            "baseline": {
                "tax_due_est_krw": int(getattr(baseline_est, "tax_due_est_krw", 0) or 0),
                "buffer_target_krw": int(getattr(baseline_est, "buffer_target_krw", 0) or 0),
                "receipt_reflected_expense_krw": int(getattr(baseline_est, "receipt_reflected_expense_krw", 0) or 0),
                "receipt_pending_expense_krw": int(getattr(baseline_est, "receipt_pending_expense_krw", 0) or 0),
            },
            "cases": {
                spec.case_id: {
                    "tx_id": int(tx_map[spec.case_id].id),
                    "counterparty": spec.counterparty,
                    "memo": spec.memo,
                    "amount_krw": spec.amount_krw,
                    "occurred_at": spec.occurred_at.isoformat(sep=" ", timespec="minutes"),
                    "initial_level": _decision_for(tx_map[spec.case_id])["level"],
                    "expectation": spec.expectation,
                }
                for spec in CASE_SPECS
            },
            "expected_transitions": {
                "followup_reflect_transport": {
                    "after_followup_level": followup_transport_after["level"],
                },
                "reinforcement_reflect_meal": {
                    "after_followup_level": meal_after_followup["level"],
                    "after_reinforcement_level": meal_after_reinforcement["level"],
                },
                "pending_cafe_no_change": {
                    "after_followup_level": pending_after_followup["level"],
                },
                "consult_asset": {
                    "after_followup_level": consult_after_followup["level"],
                },
                "reduced_motion_transport": {
                    "after_followup_level": reduced_motion_after["level"],
                },
            },
            "e2e_results": {},
        }

        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        FAILURES_PATH.write_text("[]\n", encoding="utf-8")
        print(f"seeded {SEED_EMAIL} user_pk={user.id} month={MONTH_KEY}")
        print(f"summary={SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
