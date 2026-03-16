# domain/models.py
from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Date
from sqlalchemy.dialects.postgresql import JSONB
from werkzeug.security import generate_password_hash, check_password_hash

from core.extensions import db
from core.time import utcnow


# =========================
#          User
# =========================
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    # 0a656...에서 email 길이 120 + unique
    email = db.Column(db.String(120), nullable=False, unique=True)

    # 0a656...에서 password_hash 길이 256
    password_hash = db.Column(db.String(256), nullable=False)

    # free: 기본 체험, pro: 자동화 기능
    plan = db.Column(db.String(16), nullable=False, default="free")
    # 확장 플랜 권한 source of truth (legacy `plan`은 호환 유지)
    plan_code = db.Column(db.String(16), nullable=False, default="free")
    plan_status = db.Column(db.String(16), nullable=False, default="active")
    extra_account_slots = db.Column(db.Integer, nullable=False, default=0)
    plan_updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    # ✅ (호환용) 예전 코드가 nickname/birthdate를 참조해도 죽지 않게
    @property
    def nickname(self) -> str:
        # 저장 컬럼은 없으니, 임시 표시용
        return (self.email.split("@")[0] if self.email else f"user{self.id}")

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class TaxProfile(db.Model):
    __tablename__ = "tax_profiles"

    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    profile_json = db.Column(JSONB, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# =========================
#   Import / Transactions
# =========================
class ImportJob(db.Model):
    __tablename__ = "import_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # 0a656...에서 source 길이 32
    source = db.Column(db.String(32), nullable=False, default="csv")
    filename = db.Column(db.String(255), nullable=True)

    total_rows = db.Column(db.Integer, nullable=False, default=0)
    inserted_rows = db.Column(db.Integer, nullable=False, default=0)
    duplicate_rows = db.Column(db.Integer, nullable=False, default=0)
    failed_rows = db.Column(db.Integer, nullable=False, default=0)

    # 0a656...에서 nullable=True로 변경
    error_summary = db.Column(JSONB, nullable=True)

    # 0a656...에서 created_at 추가
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        Index("idx_import_jobs_user_time", "user_pk", "started_at"),
    )


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    import_job_id = db.Column(db.Integer, db.ForeignKey("import_jobs.id"), nullable=True)

    occurred_at = db.Column(db.DateTime, nullable=False)
    direction = db.Column(db.String(8), nullable=False)  # 'in' / 'out'

    # 0a656... BIGINT -> INTEGER
    amount_krw = db.Column(db.Integer, nullable=False)

    # 0a656... TEXT -> String(255)
    counterparty = db.Column(db.String(255), nullable=True)
    memo = db.Column(db.Text, nullable=True)

    source = db.Column(db.String(32), nullable=False, default="csv")
    review_state = db.Column(db.String(16), nullable=False, default="todo")  # todo/hold/done
    bank_account_id = db.Column(db.Integer, db.ForeignKey("user_bank_accounts.id"), nullable=True, index=True)

    # 0a656... 길이 64
    external_hash = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="ck_transactions_direction"),
        CheckConstraint("amount_krw > 0", name="ck_transactions_amount_positive"),
        CheckConstraint("review_state IN ('todo','hold','done')", name="ck_transactions_review_state"),
        UniqueConstraint("user_pk", "external_hash", name="uq_tx_user_hash"),
        Index("idx_tx_user_direction", "user_pk", "direction"),
        Index("idx_tx_user_occurred", "user_pk", "occurred_at"),
        Index("idx_tx_user_occurred_account", "user_pk", "occurred_at", "bank_account_id"),
    )


# =========================
#   Labels / Rules
# =========================
class IncomeLabel(db.Model):
    __tablename__ = "income_labels"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False, unique=True)

    status = db.Column(db.String(16), nullable=False, default="unknown")   # income/non_income/unknown
    confidence = db.Column(db.Integer, nullable=False, default=0)          # 0~100
    labeled_by = db.Column(db.String(8), nullable=False, default="auto")   # auto/user
    rule_version = db.Column(db.Integer, nullable=False, default=1)

    decided_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('income','non_income','unknown')", name="ck_income_labels_status"),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_income_labels_confidence"),
        CheckConstraint("labeled_by IN ('auto','user')", name="ck_income_labels_labeled_by"),
        Index("idx_income_labels_user_status", "user_pk", "status"),
    )


class ExpenseLabel(db.Model):
    __tablename__ = "expense_labels"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False, unique=True)

    status = db.Column(db.String(16), nullable=False, default="unknown")   # business/personal/mixed/unknown
    confidence = db.Column(db.Integer, nullable=False, default=0)          # 0~100
    labeled_by = db.Column(db.String(8), nullable=False, default="auto")   # auto/user
    rule_version = db.Column(db.Integer, nullable=False, default=1)

    decided_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("labeled_by IN ('auto','user')", name="ck_expense_labels_labeled_by"),
        CheckConstraint("status IN ('business','personal','mixed','unknown')", name="ck_expense_labels_status"),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_expense_labels_confidence"),
        Index("idx_expense_labels_user_status", "user_pk", "status"),
    )


class CounterpartyRule(db.Model):
    __tablename__ = "counterparty_rules"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    counterparty_key = db.Column(db.String(255), nullable=False)

    rule = db.Column(db.String(16), nullable=False)  # income/non_income
    active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("rule IN ('income','non_income')", name="ck_rules_rule"),
        UniqueConstraint("user_pk", "counterparty_key", name="uq_rules_user_counterparty"),
        Index("idx_rules_user_active", "user_pk", "active"),
    )


class CounterpartyExpenseRule(db.Model):
    __tablename__ = "counterparty_expense_rules"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    counterparty_key = db.Column(db.String(255), nullable=False)

    rule = db.Column(db.String(16), nullable=False)  # business/personal
    active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("rule IN ('business','personal')", name="ck_exp_rules_rule"),
        UniqueConstraint("user_pk", "counterparty_key", name="uq_exp_rules_user_counterparty"),
        Index("idx_exp_rules_user_active", "user_pk", "active"),
    )


# =========================
#   Inbox / Evidence / Tasks
# =========================
class EvidenceItem(db.Model):
    __tablename__ = "evidence_items"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False, unique=True)

    requirement = db.Column(db.String(16), nullable=False)  # required/maybe/not_needed
    status = db.Column(db.String(16), nullable=False)       # missing/attached/not_needed
    note = db.Column(db.Text, nullable=True)

    # --- evidence file vault ---
    # file_key: evidence_root() 기준 상대경로 키
    file_key = db.Column(db.String(512), nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(120), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    retention_until = db.Column(db.Date, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("requirement IN ('required','maybe','not_needed')", name="ck_evidence_requirement"),
        CheckConstraint("status IN ('missing','attached','not_needed')", name="ck_evidence_status"),
        Index("idx_evidence_user_req", "user_pk", "requirement"),
        Index("idx_evidence_user_status", "user_pk", "status"),
        Index("idx_evidence_user_retention", "user_pk", "retention_until"),
    )


class ReceiptExpenseFollowupAnswer(db.Model):
    __tablename__ = "receipt_expense_followup_answers"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False)
    evidence_item_id = db.Column(db.Integer, db.ForeignKey("evidence_items.id"), nullable=True)

    question_key = db.Column(db.String(64), nullable=False)
    answer_value = db.Column(db.String(64), nullable=True)
    answer_text = db.Column(db.Text, nullable=True)
    answered_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    answered_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_pk",
            "transaction_id",
            "question_key",
            name="uq_receipt_expense_followup_user_tx_question",
        ),
        Index("idx_receipt_followup_user_tx", "user_pk", "transaction_id"),
        Index("idx_receipt_followup_evidence", "evidence_item_id"),
    )


class ReceiptExpenseReinforcement(db.Model):
    __tablename__ = "receipt_expense_reinforcements"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=False)
    evidence_item_id = db.Column(db.Integer, db.ForeignKey("evidence_items.id"), nullable=True)

    business_context_note = db.Column(db.Text, nullable=True)
    attendee_names = db.Column(db.Text, nullable=True)
    client_or_counterparty_name = db.Column(db.String(255), nullable=True)
    ceremonial_relation_note = db.Column(db.Text, nullable=True)
    asset_usage_note = db.Column(db.Text, nullable=True)
    weekend_or_late_night_note = db.Column(db.Text, nullable=True)

    supporting_file_key = db.Column(db.String(512), nullable=True)
    supporting_file_name = db.Column(db.String(255), nullable=True)
    supporting_file_mime_type = db.Column(db.String(120), nullable=True)
    supporting_file_size_bytes = db.Column(db.Integer, nullable=True)
    supporting_file_uploaded_at = db.Column(db.DateTime, nullable=True)

    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_pk",
            "transaction_id",
            name="uq_receipt_expense_reinforcement_user_tx",
        ),
        Index("idx_receipt_reinforcement_user_tx", "user_pk", "transaction_id"),
        Index("idx_receipt_reinforcement_evidence", "evidence_item_id"),
    )


class ReceiptBatch(db.Model):
    __tablename__ = "receipt_batches"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    month_key = db.Column(db.String(7), nullable=True)  # YYYY-MM
    status = db.Column(db.String(20), nullable=False, default="queued")  # queued/processing/done/done_with_errors
    total_count = db.Column(db.Integer, nullable=False, default=0)
    done_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('queued','processing','done','done_with_errors')", name="ck_receipt_batches_status"),
        Index("idx_receipt_batches_user_created", "user_pk", "created_at"),
        Index("idx_receipt_batches_user_status", "user_pk", "status"),
    )


class ReceiptItem(db.Model):
    __tablename__ = "receipt_items"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("receipt_batches.id"), nullable=False)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    file_key = db.Column(db.String(512), nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(120), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)

    status = db.Column(db.String(20), nullable=False, default="uploaded")  # uploaded/processing/done/failed
    error_message = db.Column(db.Text, nullable=True)
    receipt_type = db.Column(db.String(24), nullable=True)
    parsed_json = db.Column(JSONB, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('uploaded','processing','done','failed')", name="ck_receipt_items_status"),
        Index("idx_receipt_items_batch", "batch_id"),
        Index("idx_receipt_items_user_status", "user_pk", "status"),
        Index("idx_receipt_items_user_sha", "user_pk", "sha256"),
    )


class OfficialDataDocument(db.Model):
    __tablename__ = "official_data_documents"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    source_system = db.Column(db.String(24), nullable=False)
    document_type = db.Column(db.String(64), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)

    file_name_original = db.Column(db.String(255), nullable=False)
    file_mime_type = db.Column(db.String(120), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False, default=0)
    file_hash = db.Column(db.String(64), nullable=False)

    parser_version = db.Column(db.String(48), nullable=False, default="official_data_parser_v1")
    parse_status = db.Column(db.String(24), nullable=False, default="uploaded")
    parse_error_code = db.Column(db.String(64), nullable=True)
    parse_error_detail = db.Column(db.Text, nullable=True)

    extracted_payload_json = db.Column(JSONB, nullable=False, default=dict)
    extracted_key_summary_json = db.Column(JSONB, nullable=False, default=dict)

    document_issued_at = db.Column(db.DateTime, nullable=True)
    document_period_start = db.Column(Date, nullable=True)
    document_period_end = db.Column(Date, nullable=True)
    verified_reference_date = db.Column(Date, nullable=True)
    trust_grade = db.Column(db.String(1), nullable=True)
    trust_grade_label = db.Column(db.String(64), nullable=True)
    trust_scope_label = db.Column(db.String(128), nullable=True)
    structure_validation_status = db.Column(db.String(24), nullable=False, default="not_applicable")
    verification_source = db.Column(db.String(32), nullable=True)
    verification_status = db.Column(db.String(24), nullable=False, default="none")
    verification_checked_at = db.Column(db.DateTime, nullable=True)
    verification_reference_masked = db.Column(db.String(64), nullable=True)
    user_modified_flag = db.Column(db.Boolean, nullable=False, default=False)
    sensitive_data_redacted = db.Column(db.Boolean, nullable=False, default=True)

    raw_file_storage_mode = db.Column(db.String(24), nullable=False, default="none")
    raw_file_key = db.Column(db.String(512), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    parsed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("source_system IN ('hometax','nhis')", name="ck_official_data_source_system"),
        CheckConstraint(
            "parse_status IN ('uploaded','parsed','needs_review','unsupported','failed')",
            name="ck_official_data_parse_status",
        ),
        CheckConstraint(
            "raw_file_storage_mode IN ('none','optional_saved')",
            name="ck_official_data_raw_file_storage_mode",
        ),
        CheckConstraint(
            "trust_grade IS NULL OR trust_grade IN ('A','B','C','D')",
            name="ck_official_data_trust_grade",
        ),
        CheckConstraint(
            "verification_status IN ('none','pending','succeeded','failed','not_applicable')",
            name="ck_official_data_verification_status",
        ),
        CheckConstraint(
            "structure_validation_status IN ('passed','failed','partial','not_applicable')",
            name="ck_official_data_structure_validation_status",
        ),
        Index("idx_official_data_user_parse_status", "user_pk", "parse_status"),
        Index("idx_official_data_user_source_doc", "user_pk", "source_system", "document_type"),
        Index("idx_official_data_user_reference_date", "user_pk", "verified_reference_date"),
        Index("idx_official_data_user_created", "user_pk", "created_at"),
        Index("idx_official_data_user_trust_grade", "user_pk", "trust_grade"),
        Index("idx_official_data_user_verification_status", "user_pk", "verification_status"),
    )


class ActionLog(db.Model):
    __tablename__ = "action_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action_type = db.Column(db.String(32), nullable=False, default="label_update")
    target_ids = db.Column(JSONB, nullable=False, default=list)
    before_state = db.Column(JSONB, nullable=False, default=dict)
    after_state = db.Column(JSONB, nullable=False, default=dict)
    is_reverted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('label_update','mark_unneeded','attach','bulk_update')",
            name="ck_action_logs_action_type",
        ),
        Index("idx_action_logs_user_created", "user_pk", "created_at"),
        Index("idx_action_logs_user_reverted", "user_pk", "is_reverted"),
    )


class WeeklyTask(db.Model):
    __tablename__ = "weekly_tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    week_key = db.Column(db.String(10), nullable=False)  # e.g. 2026-W06
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    cta_label = db.Column(db.String(32), nullable=False)
    cta_url = db.Column(db.String(255), nullable=False)

    is_done = db.Column(db.Boolean, nullable=False, default=False)
    done_at = db.Column(db.DateTime, nullable=True)

    meta = db.Column(JSONB, nullable=False, default=dict)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_pk", "week_key", name="uq_weekly_tasks_user_week"),
        Index("idx_weekly_tasks_user_week", "user_pk", "week_key"),
    )


class DashboardSnapshot(db.Model):
    __tablename__ = "dashboard_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    month_key = db.Column(db.String(7), nullable=False)  # YYYY-MM
    payload = db.Column(JSONB, nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("idx_snap_user_month", "user_pk", "month_key"),)


# =========================
#   Dashboard / Ledger (0a656...)
# =========================
class UserDashboardState(db.Model):
    __tablename__ = "user_dashboard_state"

    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)

    gross_income = db.Column(db.Integer, nullable=False, default=0)
    expenses = db.Column(db.Integer, nullable=False, default=0)
    rate = db.Column(db.Float, nullable=False, default=0.15)

    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class DashboardEntry(db.Model):
    __tablename__ = "dashboard_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    kind = db.Column(db.String(16), nullable=False, default="calc")
    amount = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("idx_dash_entries_user_created", "user_pk", "created_at"),)


class HoldDecision(db.Model):
    __tablename__ = "hold_decisions"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    amount_krw = db.Column(db.Integer, nullable=False, default=0)
    rate = db.Column(db.Float, nullable=False, default=0.0)
    reason = db.Column(db.String(32), nullable=False, default="manual")

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class TaxBufferLedger(db.Model):
    __tablename__ = "tax_buffer_ledger"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    delta_amount_krw = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("idx_ledger_user_created", "user_pk", "created_at"),)


# =========================
#   ✅ 핵심: SafeToSpendSettings 복구 (settings 테이블에 매핑)
# =========================
class SafeToSpendSettings(db.Model):
    """
    safe_to_spend_settings 테이블이 삭제되고 settings 테이블로 대체됨.
    그런데 코드 곳곳에서 SafeToSpendSettings라는 이름을 계속 import하므로,
    이 클래스를 settings 테이블에 매핑해서 호환을 맞춘다.
    """
    __tablename__ = "settings"

    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)

    # 새 스키마 핵심
    default_tax_rate = db.Column(db.Float, nullable=False, default=0.15)
    custom_rates = db.Column(JSONB, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # ---- 아래는 "예전 SafeToSpendSettings 필드" 호환용 ----
    # custom_rates 안에 _meta 딕셔너리로 저장/조회 (DB 컬럼 추가 없이)
    def _meta(self) -> dict:
        if not isinstance(self.custom_rates, dict):
            self.custom_rates = {}
        meta = self.custom_rates.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            self.custom_rates["_meta"] = meta
        return meta

    def _get_meta(self, key: str, default):
        return self._meta().get(key, default)

    def _set_meta(self, key: str, value) -> None:
        base = dict(self.custom_rates) if isinstance(self.custom_rates, dict) else {}
        meta = base.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        meta = dict(meta)
        meta[key] = value
        base["_meta"] = meta
        self.custom_rates = base

    @property
    def preset(self) -> str:
        return self._get_meta("preset", "base")

    @preset.setter
    def preset(self, v: str) -> None:
        self._set_meta("preset", (v or "base"))

    @property
    def rounding_unit(self) -> int:
        return int(self._get_meta("rounding_unit", 1000) or 1000)

    @rounding_unit.setter
    def rounding_unit(self, v: int) -> None:
        self._set_meta("rounding_unit", int(v or 1000))

    @property
    def min_hold_krw(self) -> int:
        return int(self._get_meta("min_hold_krw", 0) or 0)

    @min_hold_krw.setter
    def min_hold_krw(self, v: int) -> None:
        self._set_meta("min_hold_krw", int(v or 0))

    @property
    def max_hold_percent(self) -> float:
        return float(self._get_meta("max_hold_percent", 100.0) or 100.0)

    @max_hold_percent.setter
    def max_hold_percent(self, v: float) -> None:
        self._set_meta("max_hold_percent", float(v or 100.0))
    
        # ---- (추가) 건강보험료(이번 달 고지액/자동이체액) ----
    # DB 컬럼 추가 없이 custom_rates["_meta"]에 저장
    @property
    def nhi_monthly_krw(self) -> int:
        return int(self._get_meta("nhi_monthly_krw", 0) or 0)

    @nhi_monthly_krw.setter
    def nhi_monthly_krw(self, v: int) -> None:
        self._set_meta("nhi_monthly_krw", int(v or 0))

    @property
    def month_end_reminder_enabled(self) -> bool:
        return bool(self._get_meta("month_end_reminder_enabled", True))

    @month_end_reminder_enabled.setter
    def month_end_reminder_enabled(self, v: bool) -> None:
        self._set_meta("month_end_reminder_enabled", bool(v))

    @property
    def exclusions(self) -> dict:
        val = self._get_meta("exclusions", {})
        return val if isinstance(val, dict) else {}

    @exclusions.setter
    def exclusions(self, v: dict) -> None:
        self._set_meta("exclusions", v if isinstance(v, dict) else {})

    # (호환용) created_at은 settings 테이블에 없음 → updated_at로 대체
    @property
    def created_at(self):
        return self.updated_at


class BankAccountLink(db.Model):
    """사용자가 '연동해서 추적할 계좌'를 선택해 두는 테이블."""
    __tablename__ = "bank_account_links"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    bank_code = db.Column(db.String(4), nullable=False)
    account_number = db.Column(db.String(30), nullable=False)
    bank_account_id = db.Column(db.Integer, db.ForeignKey("user_bank_accounts.id"), nullable=True, index=True)

    alias = db.Column(db.String(64), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_balance_krw = db.Column(db.Integer, nullable=True)
    last_balance_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_pk", "bank_code", "account_number", name="uq_user_bankacct"),
    )


class UserBankAccount(db.Model):
    __tablename__ = "user_bank_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    bank_code = db.Column(db.String(4), nullable=True)
    account_fingerprint = db.Column(db.String(64), nullable=True)
    account_last4 = db.Column(db.String(4), nullable=True)
    alias = db.Column(db.String(64), nullable=True)
    color_hex = db.Column(db.String(16), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_pk", "account_fingerprint", name="uq_user_bank_account_fingerprint"),
        Index("idx_user_bank_accounts_user_created", "user_pk", "created_at"),
    )


class RecurringRule(db.Model):
    __tablename__ = "recurring_rules"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    direction = db.Column(db.String(8), nullable=False)  # 'in' / 'out'
    amount_krw = db.Column(db.Integer, nullable=False, default=0)

    counterparty = db.Column(db.String(255), nullable=True)
    memo = db.Column(db.Text, nullable=True)

    cadence = db.Column(db.String(16), nullable=False, default="monthly")  # monthly/weekly
    day_of_month = db.Column(db.Integer, nullable=True)  # 1~31 (monthly)
    weekday = db.Column(db.Integer, nullable=True)       # 0~6  (weekly, Monday=0)

    start_date = db.Column(Date, nullable=False, default=lambda: utcnow().date())
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="ck_rr_direction"),
        CheckConstraint("amount_krw >= 0", name="ck_rr_amount_nonneg"),
        CheckConstraint("cadence IN ('monthly','weekly')", name="ck_rr_cadence"),
        Index("idx_rr_user_active", "user_pk", "is_active"),
    )


class RecurringCandidate(db.Model):
    __tablename__ = "recurring_candidates"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    direction = db.Column(db.String(8), nullable=False)  # in/out
    counterparty = db.Column(db.String(255), nullable=False)
    amount_bucket = db.Column(db.Integer, nullable=False, default=0)
    cadence = db.Column(db.String(16), nullable=False, default="monthly")
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="ck_rc_direction"),
        CheckConstraint("amount_bucket >= 0", name="ck_rc_amount_nonneg"),
        CheckConstraint("cadence IN ('monthly')", name="ck_rc_cadence"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rc_confidence_range"),
        UniqueConstraint("user_pk", "direction", "counterparty", "amount_bucket", "cadence", name="uq_rc_user_key"),
        Index("idx_rc_user_conf", "user_pk", "confidence"),
        Index("idx_rc_user_seen", "user_pk", "last_seen_at"),
    )


class CsvFormatMapping(db.Model):
    """사용자별 CSV 포맷(헤더 시그니처) → 매핑을 기억."""
    __tablename__ = "csv_format_mappings"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # 헤더/컬럼수/구분자 기반 sha256 hex
    signature = db.Column(db.String(64), nullable=False)

    # {date, amount, in_amount, out_amount, direction, counterparty, memo}
    mapping = db.Column(JSONB, nullable=False)

    delimiter = db.Column(db.String(4), nullable=True)
    meta = db.Column(JSONB, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_pk", "signature", name="uq_csv_map_user_sig"),
        Index("idx_csv_map_user", "user_pk"),
    )


class Inquiry(db.Model):
    __tablename__ = "inquiries"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="open")  # open/answered/closed

    admin_reply = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    replied_at = db.Column(db.DateTime, nullable=True)
    last_viewed_by_user_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open','answered','closed')", name="ck_inquiries_status"),
        Index("idx_inquiries_user_created", "user_pk", "created_at"),
        Index("idx_inquiries_status_created", "status", "created_at"),
    )


class RefreshToken(db.Model):
    __tablename__ = "refresh_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    replaced_by_id = db.Column(db.Integer, db.ForeignKey("refresh_tokens.id"), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)

    __table_args__ = (
        Index("idx_refresh_tokens_user_expires", "user_pk", "expires_at"),
        Index("idx_refresh_tokens_user_revoked", "user_pk", "revoked_at"),
    )


class NhisRateSnapshot(db.Model):
    __tablename__ = "nhis_rate_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    effective_year = db.Column(db.Integer, nullable=False)
    health_insurance_rate = db.Column(db.Numeric(10, 6), nullable=False)
    long_term_care_ratio_of_health = db.Column(db.Numeric(10, 6), nullable=False)
    long_term_care_rate_optional = db.Column(db.Numeric(10, 6), nullable=True)
    regional_point_value = db.Column(db.Numeric(12, 3), nullable=False)
    property_basic_deduction_krw = db.Column(db.Integer, nullable=False)
    car_premium_enabled = db.Column(db.Boolean, nullable=False, default=False)
    income_reference_rule = db.Column(db.String(255), nullable=False, default="")
    sources_json = db.Column(JSONB, nullable=False, default=dict)
    fetched_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("effective_year", name="uq_nhis_snapshot_year"),
        Index("idx_nhis_snapshot_year", "effective_year"),
        Index("idx_nhis_snapshot_active", "is_active", "effective_year"),
    )


class NhisUserProfile(db.Model):
    __tablename__ = "nhis_user_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)

    member_type = db.Column(db.String(16), nullable=False, default="unknown")
    target_month = db.Column(db.String(7), nullable=False, default="")
    household_has_others = db.Column(db.Boolean, nullable=True)
    annual_income_krw = db.Column(db.Integer, nullable=True)
    salary_monthly_krw = db.Column(db.Integer, nullable=True)
    non_salary_annual_income_krw = db.Column(db.Integer, nullable=True)
    property_tax_base_total_krw = db.Column(db.Integer, nullable=True)
    rent_deposit_krw = db.Column(db.Integer, nullable=True)
    rent_monthly_krw = db.Column(db.Integer, nullable=True)
    has_reduction_or_relief = db.Column(db.Boolean, nullable=True)
    has_housing_loan_deduction = db.Column(db.Boolean, nullable=True)
    last_bill_total_krw = db.Column(db.Integer, nullable=True)
    last_bill_health_only_krw = db.Column(db.Integer, nullable=True)
    last_bill_score_points = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "member_type IN ('regional','employee','dependent','unknown')",
            name="ck_nhis_user_profiles_member_type",
        ),
        Index("idx_nhis_user_profiles_user", "user_pk"),
        Index("idx_nhis_user_profiles_target_month", "target_month"),
    )


class NhisBillHistory(db.Model):
    __tablename__ = "nhis_bill_history"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    bill_year = db.Column(db.Integer, nullable=False)
    # 0이면 "연간 요약", 1~12면 월별 입력
    bill_month = db.Column(db.Integer, nullable=False, default=0)
    total_krw = db.Column(db.Integer, nullable=True)
    health_only_krw = db.Column(db.Integer, nullable=True)
    score_points = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("bill_year >= 2000 AND bill_year <= 2100", name="ck_nhis_bill_history_year"),
        CheckConstraint("bill_month >= 0 AND bill_month <= 12", name="ck_nhis_bill_history_month"),
        CheckConstraint("total_krw IS NULL OR total_krw >= 0", name="ck_nhis_bill_history_total_nonneg"),
        CheckConstraint(
            "health_only_krw IS NULL OR health_only_krw >= 0",
            name="ck_nhis_bill_history_health_nonneg",
        ),
        CheckConstraint("score_points IS NULL OR score_points >= 0", name="ck_nhis_bill_history_score_nonneg"),
        UniqueConstraint("user_pk", "bill_year", "bill_month", name="uq_nhis_bill_history_user_year_month"),
        Index("idx_nhis_bill_history_user_year", "user_pk", "bill_year"),
        Index("idx_nhis_bill_history_user_updated", "user_pk", "updated_at"),
    )


class AssetProfile(db.Model):
    __tablename__ = "asset_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    household_has_others = db.Column(db.Boolean, nullable=True)
    dependents_count = db.Column(db.Integer, nullable=True)
    other_income_types_json = db.Column(JSONB, nullable=False, default=list)
    other_income_annual_krw = db.Column(db.Integer, nullable=True)

    # 최초 단계 입력의 현재 진행 상태를 복원하기 위한 최소 정보
    quiz_step = db.Column(db.Integer, nullable=False, default=1)
    housing_mode = db.Column(db.String(16), nullable=True)  # own / rent / jeonse / none / unknown
    has_car = db.Column(db.Boolean, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("dependents_count IS NULL OR dependents_count >= 0", name="ck_asset_profiles_dependents_nonneg"),
        CheckConstraint("other_income_annual_krw IS NULL OR other_income_annual_krw >= 0", name="ck_asset_profiles_other_income_nonneg"),
        CheckConstraint(
            "housing_mode IS NULL OR housing_mode IN ('own','rent','jeonse','none','unknown')",
            name="ck_asset_profiles_housing_mode",
        ),
        CheckConstraint("quiz_step >= 1 AND quiz_step <= 6", name="ck_asset_profiles_quiz_step"),
        Index("idx_asset_profiles_user", "user_pk"),
        Index("idx_asset_profiles_completed", "completed_at"),
    )


class AssetItem(db.Model):
    __tablename__ = "asset_items"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(16), nullable=False)  # car/home/rent/deposit/other
    label = db.Column(db.String(120), nullable=True)

    input_json = db.Column(JSONB, nullable=False, default=dict)
    estimated_json = db.Column(JSONB, nullable=False, default=dict)
    basis_json = db.Column(JSONB, nullable=False, default=dict)
    user_override_json = db.Column(JSONB, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('car','home','rent','deposit','other')", name="ck_asset_items_kind"),
        Index("idx_asset_items_user_kind", "user_pk", "kind"),
        Index("idx_asset_items_user_updated", "user_pk", "updated_at"),
    )


class AssetDatasetSnapshot(db.Model):
    __tablename__ = "asset_dataset_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    dataset_key = db.Column(db.String(32), nullable=False)  # vehicle/home
    source_name = db.Column(db.String(255), nullable=False)
    source_url = db.Column(db.String(500), nullable=True)
    version_year = db.Column(db.Integer, nullable=True)
    payload_json = db.Column(JSONB, nullable=False, default=dict)
    fetched_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("dataset_key", "version_year", name="uq_asset_dataset_key_year"),
        Index("idx_asset_dataset_key_active", "dataset_key", "is_active"),
        Index("idx_asset_dataset_fetched", "fetched_at"),
    )


# =========================
#   Billing Domain (source of truth for payment events)
# =========================
class BillingCustomer(db.Model):
    __tablename__ = "billing_customers"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    customer_key = db.Column(db.String(128), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="active")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="ck_billing_customers_status"),
        UniqueConstraint("user_pk", "provider", name="uq_billing_customer_user_provider"),
        UniqueConstraint("provider", "customer_key", name="uq_billing_customer_provider_key"),
        Index("idx_billing_customers_user_provider", "user_pk", "provider"),
    )


class BillingMethod(db.Model):
    __tablename__ = "billing_methods"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    billing_customer_id = db.Column(db.Integer, db.ForeignKey("billing_customers.id"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    method_type = db.Column(db.String(24), nullable=False, default="card")
    billing_key_enc = db.Column(db.Text, nullable=False)
    billing_key_hash = db.Column(db.String(64), nullable=False)
    encryption_key_version = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="active")
    issued_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("method_type IN ('card')", name="ck_billing_methods_type"),
        CheckConstraint("status IN ('active','revoked','inactive')", name="ck_billing_methods_status"),
        UniqueConstraint("provider", "billing_key_hash", name="uq_billing_methods_provider_key_hash"),
        Index("idx_billing_methods_user_status", "user_pk", "status"),
    )


class BillingMethodRegistrationAttempt(db.Model):
    __tablename__ = "billing_method_registration_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    billing_customer_id = db.Column(db.Integer, db.ForeignKey("billing_customers.id"), nullable=True, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    order_id = db.Column(db.String(64), nullable=False)
    customer_key = db.Column(db.String(128), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="registration_started")
    fail_code = db.Column(db.String(64), nullable=True)
    fail_message_norm = db.Column(db.String(255), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('registration_started','billing_key_issued','failed','canceled')",
            name="ck_billing_reg_attempts_status",
        ),
        UniqueConstraint("order_id", name="uq_billing_reg_attempts_order_id"),
        Index("idx_billing_reg_attempts_user_status", "user_pk", "status"),
    )


class CheckoutIntent(db.Model):
    __tablename__ = "billing_checkout_intents"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    intent_type = db.Column(db.String(32), nullable=False)
    target_plan_code = db.Column(db.String(16), nullable=True)
    addon_quantity = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(8), nullable=False, default="KRW")
    amount_snapshot_krw = db.Column(db.Integer, nullable=False, default=0)
    pricing_snapshot_json = db.Column(JSONB, nullable=False, default=dict)
    status = db.Column(db.String(32), nullable=False, default="created")
    requires_billing_method = db.Column(db.Boolean, nullable=False, default=True)
    billing_method_id = db.Column(db.Integer, db.ForeignKey("billing_methods.id"), nullable=True, index=True)
    related_subscription_id = db.Column(db.Integer, db.ForeignKey("billing_subscriptions.id"), nullable=True, index=True)
    idempotency_key = db.Column(db.String(64), nullable=True)
    resume_token = db.Column(db.String(128), nullable=True)
    requested_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "intent_type IN ('initial_subscription','upgrade','addon_proration')",
            name="ck_billing_checkout_intents_type",
        ),
        CheckConstraint("target_plan_code IN ('free','basic','pro') OR target_plan_code IS NULL", name="ck_billing_checkout_intents_plan"),
        CheckConstraint("addon_quantity IS NULL OR addon_quantity >= 0", name="ck_billing_checkout_intents_addon_nonneg"),
        CheckConstraint("currency IN ('KRW')", name="ck_billing_checkout_intents_currency"),
        CheckConstraint("amount_snapshot_krw >= 0", name="ck_billing_checkout_intents_amount_nonneg"),
        CheckConstraint(
            "status IN ('created','registration_required','ready_for_charge','charge_started','completed','failed','abandoned','canceled')",
            name="ck_billing_checkout_intents_status",
        ),
        UniqueConstraint("user_pk", "idempotency_key", name="uq_billing_checkout_intents_user_idempotency"),
        UniqueConstraint("resume_token", name="uq_billing_checkout_intents_resume_token"),
        Index("idx_billing_checkout_intents_user_status", "user_pk", "status"),
        Index("idx_billing_checkout_intents_user_requested", "user_pk", "requested_at"),
        Index("idx_billing_checkout_intents_expires", "expires_at"),
    )


class Subscription(db.Model):
    __tablename__ = "billing_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    billing_customer_id = db.Column(db.Integer, db.ForeignKey("billing_customers.id"), nullable=False, index=True)
    billing_method_id = db.Column(db.Integer, db.ForeignKey("billing_methods.id"), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="pending_activation")
    billing_anchor_at = db.Column(db.DateTime(timezone=True), nullable=True)
    current_period_start = db.Column(db.DateTime(timezone=True), nullable=True)
    current_period_end = db.Column(db.DateTime(timezone=True), nullable=True)
    next_billing_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    grace_until = db.Column(db.DateTime(timezone=True), nullable=True)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    last_failed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancel_requested_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancel_effective_at = db.Column(db.DateTime(timezone=True), nullable=True)
    canceled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_activation','active','grace_started','cancel_requested','canceled','past_due')",
            name="ck_billing_subscriptions_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_billing_subscriptions_retry_nonneg"),
        Index("idx_billing_subscriptions_user_status", "user_pk", "status"),
        Index("idx_billing_subscriptions_next_billing", "next_billing_at"),
    )


class SubscriptionItem(db.Model):
    __tablename__ = "billing_subscription_items"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("billing_subscriptions.id"), nullable=False, index=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    item_type = db.Column(db.String(32), nullable=False)  # plan_base / addon_account_slot
    item_code = db.Column(db.String(32), nullable=False)  # free/basic/pro/addon_account_slot
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price_krw = db.Column(db.Integer, nullable=False, default=0)
    amount_krw = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(24), nullable=False, default="active")
    effective_from = db.Column(db.DateTime(timezone=True), nullable=False)
    effective_to = db.Column(db.DateTime(timezone=True), nullable=True)
    snapshot_json = db.Column(JSONB, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('plan_base','addon_account_slot')",
            name="ck_billing_subscription_items_type",
        ),
        CheckConstraint("quantity >= 0", name="ck_billing_subscription_items_qty_nonneg"),
        CheckConstraint("unit_price_krw >= 0", name="ck_billing_subscription_items_unit_nonneg"),
        CheckConstraint("amount_krw >= 0", name="ck_billing_subscription_items_amount_nonneg"),
        CheckConstraint("status IN ('active','pending','removed')", name="ck_billing_subscription_items_status"),
        Index("idx_billing_subscription_items_sub_status", "subscription_id", "status"),
        Index("idx_billing_subscription_items_user_active", "user_pk", "status"),
    )


class PaymentAttempt(db.Model):
    __tablename__ = "billing_payment_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("billing_subscriptions.id"), nullable=True, index=True)
    checkout_intent_id = db.Column(db.Integer, db.ForeignKey("billing_checkout_intents.id"), nullable=True, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    attempt_type = db.Column(db.String(40), nullable=False)
    order_id = db.Column(db.String(64), nullable=False)
    payment_key = db.Column(db.String(128), nullable=True)
    amount_krw = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="KRW")
    status = db.Column(db.String(32), nullable=False, default="charge_started")
    fail_code = db.Column(db.String(64), nullable=True)
    fail_message_norm = db.Column(db.String(255), nullable=True)
    requested_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    authorized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    failed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reconciled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "attempt_type IN ('initial','recurring','upgrade_full_charge','addon_proration','retry')",
            name="ck_billing_payment_attempts_type",
        ),
        CheckConstraint("amount_krw >= 0", name="ck_billing_payment_attempts_amount_nonneg"),
        CheckConstraint(
            "status IN ('charge_started','authorized','failed','reconciled','reconcile_needed','canceled')",
            name="ck_billing_payment_attempts_status",
        ),
        UniqueConstraint("order_id", name="uq_billing_payment_attempts_order_id"),
        UniqueConstraint("provider", "payment_key", name="uq_billing_payment_attempts_provider_payment_key"),
        Index("idx_billing_payment_attempts_user_status", "user_pk", "status"),
        Index("idx_billing_payment_attempts_sub_status", "subscription_id", "status"),
        Index("idx_billing_payment_attempts_intent_status", "checkout_intent_id", "status"),
    )


class PaymentEvent(db.Model):
    __tablename__ = "billing_payment_events"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    provider = db.Column(db.String(32), nullable=False, default="toss")
    event_type = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="received")
    transmission_id = db.Column(db.String(128), nullable=True)
    event_hash = db.Column(db.String(64), nullable=False)
    related_order_id = db.Column(db.String(64), nullable=True)
    related_payment_key = db.Column(db.String(128), nullable=True)
    payload_json = db.Column(JSONB, nullable=True, default=dict)
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('received','validated','applied','ignored_duplicate','failed')",
            name="ck_billing_payment_events_status",
        ),
        UniqueConstraint("provider", "transmission_id", name="uq_billing_payment_events_provider_tx"),
        UniqueConstraint("provider", "event_hash", name="uq_billing_payment_events_provider_hash"),
        Index("idx_billing_payment_events_order", "related_order_id"),
        Index("idx_billing_payment_events_payment_key", "related_payment_key"),
    )


class EntitlementChangeLog(db.Model):
    __tablename__ = "entitlement_change_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    source_type = db.Column(db.String(32), nullable=False)
    source_id = db.Column(db.String(64), nullable=False)
    before_json = db.Column(JSONB, nullable=False, default=dict)
    after_json = db.Column(JSONB, nullable=False, default=dict)
    reason = db.Column(db.String(255), nullable=True)
    applied_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_pk", "source_type", "source_id", name="uq_entitlement_change_logs_source"),
        Index("idx_entitlement_change_logs_user_applied", "user_pk", "applied_at"),
    )


# ✅ 코드 호환: 어떤 파일은 Settings라는 이름을 import할 수도 있음
Settings = SafeToSpendSettings
