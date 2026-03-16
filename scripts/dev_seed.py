#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect

# Allow "python scripts/dev_seed.py" from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from core.extensions import db
from core.time import utcnow
from domain.models import (
    ActionLog,
    AssetItem,
    AssetProfile,
    BankAccountLink,
    CounterpartyExpenseRule,
    CounterpartyRule,
    CsvFormatMapping,
    DashboardEntry,
    DashboardSnapshot,
    EvidenceItem,
    ExpenseLabel,
    HoldDecision,
    ImportJob,
    IncomeLabel,
    Inquiry,
    ReceiptBatch,
    ReceiptItem,
    RecurringCandidate,
    RecurringRule,
    Settings,
    TaxBufferLedger,
    TaxProfile,
    Transaction,
    User,
    UserDashboardState,
    WeeklyTask,
)
from services.auth import register_user
from services.onboarding import save_onboarding, save_tax_profile

TEST_EMAIL = "test+local@safetospend.local"
TEST_PASSWORD = "Test1234!"
ADMIN_TEST_EMAIL = "admin+local@safetospend.local"
ADMIN_TEST_PASSWORD = "Admin1234!"


def _delete_user_data(user_pk: int) -> None:
    inspector = inspect(db.engine)
    has_receipt_items = inspector.has_table("receipt_items")
    has_receipt_batches = inspector.has_table("receipt_batches")
    has_action_logs = inspector.has_table("action_logs")
    has_recurring_candidates = inspector.has_table("recurring_candidates")
    has_inquiries = inspector.has_table("inquiries")
    has_asset_profiles = inspector.has_table("asset_profiles")
    has_asset_items = inspector.has_table("asset_items")

    IncomeLabel.query.filter(IncomeLabel.user_pk == user_pk).delete(synchronize_session=False)
    ExpenseLabel.query.filter(ExpenseLabel.user_pk == user_pk).delete(synchronize_session=False)
    EvidenceItem.query.filter(EvidenceItem.user_pk == user_pk).delete(synchronize_session=False)
    if has_receipt_items:
        ReceiptItem.query.filter(ReceiptItem.user_pk == user_pk).delete(synchronize_session=False)
    if has_receipt_batches:
        ReceiptBatch.query.filter(ReceiptBatch.user_pk == user_pk).delete(synchronize_session=False)
    if has_action_logs:
        ActionLog.query.filter(ActionLog.user_pk == user_pk).delete(synchronize_session=False)
    if has_recurring_candidates:
        RecurringCandidate.query.filter(RecurringCandidate.user_pk == user_pk).delete(synchronize_session=False)
    if has_inquiries:
        Inquiry.query.filter(Inquiry.user_pk == user_pk).delete(synchronize_session=False)
    if has_asset_items:
        AssetItem.query.filter(AssetItem.user_pk == user_pk).delete(synchronize_session=False)
    if has_asset_profiles:
        AssetProfile.query.filter(AssetProfile.user_pk == user_pk).delete(synchronize_session=False)

    CounterpartyRule.query.filter(CounterpartyRule.user_pk == user_pk).delete(synchronize_session=False)
    CounterpartyExpenseRule.query.filter(CounterpartyExpenseRule.user_pk == user_pk).delete(synchronize_session=False)
    WeeklyTask.query.filter(WeeklyTask.user_pk == user_pk).delete(synchronize_session=False)
    DashboardSnapshot.query.filter(DashboardSnapshot.user_pk == user_pk).delete(synchronize_session=False)
    DashboardEntry.query.filter(DashboardEntry.user_pk == user_pk).delete(synchronize_session=False)
    HoldDecision.query.filter(HoldDecision.user_pk == user_pk).delete(synchronize_session=False)
    TaxBufferLedger.query.filter(TaxBufferLedger.user_pk == user_pk).delete(synchronize_session=False)
    UserDashboardState.query.filter(UserDashboardState.user_pk == user_pk).delete(synchronize_session=False)
    BankAccountLink.query.filter(BankAccountLink.user_pk == user_pk).delete(synchronize_session=False)
    RecurringRule.query.filter(RecurringRule.user_pk == user_pk).delete(synchronize_session=False)
    CsvFormatMapping.query.filter(CsvFormatMapping.user_pk == user_pk).delete(synchronize_session=False)
    Transaction.query.filter(Transaction.user_pk == user_pk).delete(synchronize_session=False)
    ImportJob.query.filter(ImportJob.user_pk == user_pk).delete(synchronize_session=False)
    db.session.commit()


def _ensure_user() -> User:
    user = User.query.filter_by(email=TEST_EMAIL).first()
    if not user:
        ok, msg = register_user(TEST_EMAIL, TEST_PASSWORD)
        if not ok:
            raise RuntimeError(f"테스트 계정 생성 실패: {msg}")
        user = User.query.filter_by(email=TEST_EMAIL).first()
        if not user:
            raise RuntimeError("테스트 계정 생성 후 조회 실패")
    else:
        user.set_password(TEST_PASSWORD)
        db.session.add(user)
        db.session.commit()
    return user


def _ensure_admin_user(*, admin_email: str, admin_password: str) -> User | None:
    email = (admin_email or "").strip().lower()
    if not email:
        return None
    user = User.query.filter_by(email=email).first()
    if not user:
        try:
            ok, msg = register_user(email, admin_password)
            if not ok and "이미 가입된 이메일" not in str(msg):
                raise RuntimeError(f"관리자 계정 생성 실패: {msg}")
        except Exception:
            db.session.rollback()
        user = User.query.filter_by(email=email).first()
        if not user:
            raise RuntimeError("관리자 계정 생성 후 조회 실패")
    else:
        user.set_password(admin_password)
    user.is_admin = True
    db.session.add(user)
    db.session.commit()
    return user


def _ensure_defaults(user_pk: int) -> None:
    st = db.session.get(Settings, user_pk)
    if not st:
        st = Settings(user_pk=user_pk, default_tax_rate=0.15, custom_rates={})
    st.default_tax_rate = 0.15
    st.nhi_monthly_krw = 100000
    db.session.add(st)
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

    save_tax_profile(
        user_pk=user_pk,
        payload={
            "industry_group": "it",
            "industry_text": "",
            "tax_type": "unknown",
            "prev_income_band": "unknown",
            "withholding_3_3": "yes",
            "other_income": "yes",
            "other_income_types": ["other"],
            "health_insurance_type": "regional",
            "health_insurance_monthly_krw": 100000,
            "wizard_last_step": 3,
            "profile_flow_done": True,
        },
    )

    inspector = inspect(db.engine)
    if inspector.has_table("inquiries"):
        existing = Inquiry.query.filter_by(user_pk=user_pk).count()
        if existing <= 0:
            db.session.add(
                Inquiry(
                    user_pk=user_pk,
                    subject="테스트 문의: 영수증 처리 속도",
                    message="다중 업로드 후 처리 상태가 어떻게 보이는지 확인하려고 남긴 테스트 문의입니다.",
                    status="open",
                )
            )
            db.session.add(
                Inquiry(
                    user_pk=user_pk,
                    subject="테스트 문의: 세금 보관함 안내 문구",
                    message="세금 보관 권장액 설명 문구가 이해하기 쉬운지 확인 부탁드립니다.",
                    status="answered",
                    admin_reply="테스트 답변입니다. 문의 상세 화면 표시 확인용이에요.",
                )
            )
            db.session.commit()

    if inspector.has_table("asset_profiles") and inspector.has_table("asset_items"):
        ap = AssetProfile.query.filter_by(user_pk=user_pk).first()
        if not ap:
            ap = AssetProfile(
                user_pk=user_pk,
                household_has_others=False,
                dependents_count=1,
                other_income_types_json=["business"],
                other_income_annual_krw=12000000,
                quiz_step=6,
                housing_mode="own",
                has_car=True,
                completed_at=utcnow(),
            )
            db.session.add(ap)
            db.session.flush()

        car = AssetItem.query.filter_by(user_pk=user_pk, kind="car").first()
        if not car:
            car = AssetItem(
                user_pk=user_pk,
                kind="car",
                label="차량",
                input_json={"brand": "현대", "model": "아반떼", "year": 2021},
                estimated_json={},
                basis_json={},
            )
            db.session.add(car)

        home = AssetItem.query.filter_by(user_pk=user_pk, kind="home").first()
        if not home:
            home = AssetItem(
                user_pk=user_pk,
                kind="home",
                label="부동산",
                input_json={"address_text": "서울 마포구 상암동", "home_type": "apartment", "area_sqm": 59},
                estimated_json={},
                basis_json={},
            )
            db.session.add(home)

        rent = AssetItem.query.filter_by(user_pk=user_pk, kind="rent").first()
        if not rent:
            rent = AssetItem(
                user_pk=user_pk,
                kind="rent",
                label="전월세",
                input_json={"rent_deposit_krw": 30000000, "rent_monthly_krw": 700000},
                estimated_json={},
                basis_json={},
            )
            db.session.add(rent)

        db.session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="SafeToSpend 로컬 검증용 테스트 계정/기본 상태 시드")
    parser.add_argument(
        "--reset-data",
        action="store_true",
        help="테스트 계정의 거래/라벨/증빙/작업 데이터를 초기화합니다.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        user = _ensure_user()
        admin_email = ADMIN_TEST_EMAIL
        admin_password = ADMIN_TEST_PASSWORD
        admin_user = _ensure_admin_user(admin_email=admin_email, admin_password=admin_password)
        if args.reset_data:
            _delete_user_data(user.id)
        _ensure_defaults(user.id)

        print("OK: dev seed complete")
        print(f"email={TEST_EMAIL}")
        print(f"password={TEST_PASSWORD}")
        print(f"user_id={user.id}")
        if admin_user:
            print(f"admin_email={admin_email}")
            print(f"admin_password={admin_password}")
            print(f"admin_user_id={admin_user.id}")
        if args.reset_data:
            print("mode=reset-data")
        else:
            print("mode=upsert")
        print("sample_csv=sample_data/sample_bank.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
