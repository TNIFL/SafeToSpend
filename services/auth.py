# services/auth.py
from __future__ import annotations

import logging

from sqlalchemy import inspect

from core.extensions import db
from domain.models import (
    ActionLog,
    AssetItem,
    AssetProfile,
    BankAccountLink,
    BillingCustomer,
    BillingMethod,
    BillingMethodRegistrationAttempt,
    CheckoutIntent,
    CounterpartyExpenseRule,
    CounterpartyRule,
    CsvFormatMapping,
    DashboardEntry,
    DashboardSnapshot,
    EntitlementChangeLog,
    EvidenceItem,
    ExpenseLabel,
    HoldDecision,
    ImportJob,
    IncomeLabel,
    Inquiry,
    NhisBillHistory,
    NhisUserProfile,
    OfficialDataDocument,
    PaymentAttempt,
    PaymentEvent,
    ReceiptBatch,
    ReceiptExpenseFollowupAnswer,
    ReceiptExpenseReinforcement,
    ReceiptItem,
    RecurringCandidate,
    RecurringRule,
    RefreshToken,
    Settings,
    Subscription,
    SubscriptionItem,
    TaxProfile,
    TaxBufferLedger,
    Transaction,
    User,
    UserBankAccount,
    UserDashboardState,
    WeeklyTask,
)
from services.evidence_vault import delete_physical_file

logger = logging.getLogger(__name__)


def register_user(email: str, password: str) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False, "올바른 이메일을 입력해 주세요."
    if not password or len(password) < 8:
        return False, "비밀번호는 8자 이상으로 설정해 주세요."

    if User.query.filter_by(email=email).first():
        return False, "이미 가입된 이메일입니다."

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()  # user.id 확보

    # settings는 0a656...에서 safe_to_spend_settings 대신 새로 생김
    st = Settings(user_pk=user.id, default_tax_rate=0.15, custom_rates={})
    db.session.add(st)

    db.session.commit()
    return True, "가입 완료"


def authenticate(identifier: str, password: str) -> tuple[bool, str, int | None]:
    # 이제 닉네임/생년월일 컬럼이 DB에 없음 → 이메일만 받는 게 안전
    email = (identifier or "").strip().lower()

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password or ""):
        return False, "이메일 또는 비밀번호가 올바르지 않습니다.", None

    return True, "ok", user.id


def change_user_password(
    *,
    user_pk: int,
    current_password: str,
    new_password: str,
    new_password_confirm: str,
) -> tuple[bool, str]:
    user = User.query.filter_by(id=user_pk).first()
    if not user:
        return False, "계정을 찾을 수 없습니다. 다시 로그인해 주세요."

    if not user.check_password(current_password or ""):
        return False, "현재 비밀번호가 올바르지 않습니다."

    if not new_password or len(new_password) < 8:
        return False, "새 비밀번호는 8자 이상으로 입력해 주세요."

    if new_password != (new_password_confirm or ""):
        return False, "새 비밀번호 확인이 일치하지 않습니다."

    if current_password == new_password:
        return False, "현재 비밀번호와 다른 비밀번호를 사용해 주세요."

    user.set_password(new_password)
    db.session.add(user)
    db.session.commit()
    return True, "비밀번호가 변경되었습니다."


def set_user_admin_role(*, email: str, is_admin: bool) -> tuple[bool, str, int | None]:
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        return False, "올바른 이메일을 입력해 주세요.", None

    user = User.query.filter_by(email=normalized).first()
    if not user:
        return False, "대상 사용자를 찾을 수 없습니다.", None

    user.is_admin = bool(is_admin)
    db.session.add(user)
    db.session.commit()
    return True, "관리자 권한이 변경되었습니다.", int(user.id)


def delete_user_account(
    *,
    user_pk: int,
    current_password: str,
    confirm_text: str,
) -> tuple[bool, str, int]:
    user = User.query.filter_by(id=user_pk).first()
    if not user:
        return False, "계정을 찾을 수 없습니다. 다시 로그인해 주세요.", 0

    if not user.check_password(current_password or ""):
        return False, "현재 비밀번호가 올바르지 않습니다.", 0

    token = (confirm_text or "").strip().lower()
    if token not in {"삭제", "delete"}:
        return False, "확인 문구가 일치하지 않습니다. '삭제'를 입력해 주세요.", 0

    def _has_table(name: str) -> bool:
        try:
            return bool(inspect(db.engine).has_table(name))
        except Exception:
            return False

    table_ready = {
        name: _has_table(name)
        for name in {
            "action_logs",
            "asset_items",
            "asset_profiles",
            "bank_account_links",
            "billing_checkout_intents",
            "billing_customers",
            "billing_method_registration_attempts",
            "billing_methods",
            "billing_payment_attempts",
            "billing_payment_events",
            "billing_subscription_items",
            "billing_subscriptions",
            "counterparty_expense_rules",
            "counterparty_rules",
            "csv_format_mappings",
            "dashboard_entries",
            "dashboard_snapshots",
            "entitlement_change_logs",
            "evidence_items",
            "hold_decisions",
            "import_jobs",
            "inquiries",
            "nhis_bill_history",
            "nhis_user_profiles",
            "official_data_documents",
            "receipt_batches",
            "receipt_expense_followup_answers",
            "receipt_expense_reinforcements",
            "receipt_items",
            "recurring_candidates",
            "recurring_rules",
            "refresh_tokens",
            "settings",
            "tax_buffer_ledger",
            "tax_profiles",
            "transactions",
            "user_bank_accounts",
            "user_dashboard_state",
            "weekly_tasks",
        }
    }

    file_keys = set()
    try:
        if table_ready["evidence_items"]:
            for (fk,) in (
                db.session.query(EvidenceItem.file_key)
                .filter(EvidenceItem.user_pk == user_pk)
                .filter(EvidenceItem.file_key.isnot(None))
                .all()
            ):
                if fk:
                    file_keys.add(str(fk))
        if table_ready["receipt_items"]:
            for (fk,) in (
                db.session.query(ReceiptItem.file_key)
                .filter(ReceiptItem.user_pk == user_pk)
                .filter(ReceiptItem.file_key.isnot(None))
                .all()
            ):
                if fk:
                    file_keys.add(str(fk))
        if table_ready["receipt_expense_reinforcements"]:
            for (fk,) in (
                db.session.query(ReceiptExpenseReinforcement.supporting_file_key)
                .filter(ReceiptExpenseReinforcement.user_pk == user_pk)
                .filter(ReceiptExpenseReinforcement.supporting_file_key.isnot(None))
                .all()
            ):
                if fk:
                    file_keys.add(str(fk))
        if table_ready["official_data_documents"]:
            for (fk,) in (
                db.session.query(OfficialDataDocument.raw_file_key)
                .filter(OfficialDataDocument.user_pk == user_pk)
                .filter(OfficialDataDocument.raw_file_key.isnot(None))
                .all()
            ):
                if fk:
                    file_keys.add(str(fk))
    except Exception:
        db.session.rollback()
        return False, "계정 삭제 준비 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 0

    try:
        # nullable 감사 참조는 먼저 끊어야 다른 사용자의 데이터를 보존한 채 탈퇴를 처리할 수 있다.
        if table_ready["receipt_expense_followup_answers"]:
            ReceiptExpenseFollowupAnswer.query.filter(
                ReceiptExpenseFollowupAnswer.answered_by == user_pk
            ).update({"answered_by": None}, synchronize_session=False)
        if table_ready["receipt_expense_reinforcements"]:
            ReceiptExpenseReinforcement.query.filter(
                ReceiptExpenseReinforcement.updated_by == user_pk
            ).update({"updated_by": None}, synchronize_session=False)
        if table_ready["refresh_tokens"]:
            RefreshToken.query.filter(RefreshToken.user_pk == user_pk).update(
                {"replaced_by_id": None},
                synchronize_session=False,
            )

        # 자식 FK를 먼저 제거한다.
        IncomeLabel.query.filter(IncomeLabel.user_pk == user_pk).delete(synchronize_session=False)
        ExpenseLabel.query.filter(ExpenseLabel.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["receipt_expense_followup_answers"]:
            ReceiptExpenseFollowupAnswer.query.filter(
                ReceiptExpenseFollowupAnswer.user_pk == user_pk
            ).delete(synchronize_session=False)
        if table_ready["receipt_expense_reinforcements"]:
            ReceiptExpenseReinforcement.query.filter(
                ReceiptExpenseReinforcement.user_pk == user_pk
            ).delete(synchronize_session=False)
        if table_ready["evidence_items"]:
            EvidenceItem.query.filter(EvidenceItem.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["receipt_items"]:
            ReceiptItem.query.filter(ReceiptItem.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_payment_attempts"]:
            PaymentAttempt.query.filter(PaymentAttempt.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_subscription_items"]:
            SubscriptionItem.query.filter(SubscriptionItem.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_checkout_intents"]:
            CheckoutIntent.query.filter(CheckoutIntent.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_payment_events"]:
            PaymentEvent.query.filter(PaymentEvent.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_method_registration_attempts"]:
            BillingMethodRegistrationAttempt.query.filter(
                BillingMethodRegistrationAttempt.user_pk == user_pk
            ).delete(synchronize_session=False)
        if table_ready["entitlement_change_logs"]:
            EntitlementChangeLog.query.filter(EntitlementChangeLog.user_pk == user_pk).delete(
                synchronize_session=False
            )
        if table_ready["receipt_batches"]:
            ReceiptBatch.query.filter(ReceiptBatch.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["action_logs"]:
            ActionLog.query.filter(ActionLog.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["recurring_candidates"]:
            RecurringCandidate.query.filter(RecurringCandidate.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["asset_items"]:
            AssetItem.query.filter(AssetItem.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["inquiries"]:
            Inquiry.query.filter(Inquiry.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["refresh_tokens"]:
            RefreshToken.query.filter(RefreshToken.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["official_data_documents"]:
            OfficialDataDocument.query.filter(OfficialDataDocument.user_pk == user_pk).delete(
                synchronize_session=False
            )
        if table_ready["nhis_bill_history"]:
            NhisBillHistory.query.filter(NhisBillHistory.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["nhis_user_profiles"]:
            NhisUserProfile.query.filter(NhisUserProfile.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["asset_profiles"]:
            AssetProfile.query.filter(AssetProfile.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_subscriptions"]:
            Subscription.query.filter(Subscription.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_methods"]:
            BillingMethod.query.filter(BillingMethod.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["billing_customers"]:
            BillingCustomer.query.filter(BillingCustomer.user_pk == user_pk).delete(synchronize_session=False)

        # 사용자 단위 보조 테이블
        if table_ready["counterparty_rules"]:
            CounterpartyRule.query.filter(CounterpartyRule.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["counterparty_expense_rules"]:
            CounterpartyExpenseRule.query.filter(
                CounterpartyExpenseRule.user_pk == user_pk
            ).delete(synchronize_session=False)
        if table_ready["weekly_tasks"]:
            WeeklyTask.query.filter(WeeklyTask.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["dashboard_snapshots"]:
            DashboardSnapshot.query.filter(DashboardSnapshot.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["dashboard_entries"]:
            DashboardEntry.query.filter(DashboardEntry.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["hold_decisions"]:
            HoldDecision.query.filter(HoldDecision.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["tax_buffer_ledger"]:
            TaxBufferLedger.query.filter(TaxBufferLedger.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["user_dashboard_state"]:
            UserDashboardState.query.filter(UserDashboardState.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["bank_account_links"]:
            BankAccountLink.query.filter(BankAccountLink.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["recurring_rules"]:
            RecurringRule.query.filter(RecurringRule.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["csv_format_mappings"]:
            CsvFormatMapping.query.filter(CsvFormatMapping.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["settings"]:
            Settings.query.filter(Settings.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["tax_profiles"]:
            TaxProfile.query.filter(TaxProfile.user_pk == user_pk).delete(synchronize_session=False)

        # 부모 FK가 남아있는 테이블은 마지막에 정리한다.
        if table_ready["transactions"]:
            Transaction.query.filter(Transaction.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["import_jobs"]:
            ImportJob.query.filter(ImportJob.user_pk == user_pk).delete(synchronize_session=False)
        if table_ready["user_bank_accounts"]:
            UserBankAccount.query.filter(UserBankAccount.user_pk == user_pk).delete(synchronize_session=False)

        User.query.filter(User.id == user_pk).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        logger.exception("account deletion db purge failed user_pk=%s", user_pk)
        db.session.rollback()
        return False, "계정 삭제 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 0

    file_delete_errors = 0
    for file_key in sorted(file_keys):
        try:
            delete_physical_file(file_key)
        except FileNotFoundError:
            logger.info("account deletion skipped missing file_key user_pk=%s file_key=%s", user_pk, file_key)
        except Exception:
            file_delete_errors += 1
            logger.warning("account deletion file cleanup failed user_pk=%s file_key=%s", user_pk, file_key, exc_info=True)

    return True, "계정이 삭제되었습니다.", file_delete_errors
