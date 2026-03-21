# domain/models.py
from __future__ import annotations

from sqlalchemy import CheckConstraint, Date, Index, UniqueConstraint, text
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


class UserConsentAgreement(db.Model):
    __tablename__ = "user_consent_agreements"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    document_type = db.Column(db.String(64), nullable=False)
    document_version = db.Column(db.String(32), nullable=False)
    agreed_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_pk", "document_type", "document_version", name="uq_user_consent_doc_version"),
        Index("idx_user_consent_user_agreed", "user_pk", "agreed_at"),
        Index("idx_user_consent_doc_version", "document_type", "document_version"),
    )


class LegalDocumentMetadata(db.Model):
    __tablename__ = "legal_document_metadata"

    id = db.Column(db.Integer, primary_key=True)
    document_type = db.Column(db.String(64), nullable=False)
    version = db.Column(db.String(32), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="draft")
    effective_at = db.Column(db.DateTime, nullable=False)
    requires_reconsent = db.Column(db.Boolean, nullable=False, default=False)
    summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('draft','active','archived')", name="ck_legal_document_metadata_status"),
        UniqueConstraint("document_type", "version", name="uq_legal_document_type_version"),
        Index(
            "uq_legal_document_active_per_type",
            "document_type",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("idx_legal_document_type_status", "document_type", "status"),
    )


# =========================
#   Import / Transactions
# =========================
class ImportJob(db.Model):
    __tablename__ = "import_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # 0a656...에서 source 길이 32
    source = db.Column(db.String(32), nullable=False, default="csv")
    provider = db.Column(db.String(32), nullable=True)
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
        Index("idx_import_jobs_user_source_provider", "user_pk", "source", "provider"),
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
    provider = db.Column(db.String(32), nullable=True)

    # 0a656... 길이 64
    external_hash = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="ck_transactions_direction"),
        CheckConstraint("amount_krw > 0", name="ck_transactions_amount_positive"),
        UniqueConstraint("user_pk", "external_hash", name="uq_tx_user_hash"),
        Index("idx_tx_user_direction", "user_pk", "direction"),
        Index("idx_tx_user_occurred", "user_pk", "occurred_at"),
        Index("idx_tx_user_source_provider", "user_pk", "source", "provider"),
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


class OfficialDataDocument(db.Model):
    __tablename__ = "official_data_documents"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    document_type = db.Column(db.String(64), nullable=True)
    source_authority = db.Column(db.String(120), nullable=True)

    raw_file_key = db.Column(db.String(512), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)

    reference_date = db.Column(Date, nullable=True)

    parse_status = db.Column(db.String(24), nullable=False, default="needs_review")
    verification_status = db.Column(db.String(24), nullable=False, default="not_verified")
    structure_validation_status = db.Column(db.String(24), nullable=False, default="unknown")
    trust_grade = db.Column(db.String(8), nullable=False, default="D")

    extracted_key_summary_json = db.Column(JSONB, nullable=True)
    parser_version = db.Column(db.String(32), nullable=False, default="official-data-v1")

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("parse_status IN ('parsed','needs_review','unsupported','failed')", name="ck_official_data_parse_status"),
        CheckConstraint(
            "verification_status IN ('not_verified','verified','verification_failed')",
            name="ck_official_data_verification_status",
        ),
        CheckConstraint(
            "structure_validation_status IN ('passed','needs_review','unsupported','failed','unknown')",
            name="ck_official_data_structure_validation_status",
        ),
        CheckConstraint("trust_grade IN ('A','B','C','D')", name="ck_official_data_trust_grade"),
        Index("idx_official_data_user_created", "user_pk", "created_at"),
        Index("idx_official_data_user_parse", "user_pk", "parse_status"),
        Index("idx_official_data_user_reference", "user_pk", "reference_date"),
    )


class ReferenceMaterialItem(db.Model):
    __tablename__ = "reference_material_items"

    id = db.Column(db.Integer, primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    material_kind = db.Column(db.String(24), nullable=False, default="reference")

    raw_file_key = db.Column(db.String(512), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("material_kind IN ('reference','note_attachment')", name="ck_reference_material_kind"),
        Index("idx_reference_material_user_created", "user_pk", "created_at"),
        Index("idx_reference_material_user_kind", "user_pk", "material_kind"),
    )


class ReceiptModalJobRecord(db.Model):
    __tablename__ = "receipt_modal_jobs"

    id = db.Column(db.String(32), primary_key=True)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(db.String(24), nullable=False, default="queued")
    storage_dir = db.Column(db.String(1024), nullable=False)
    parse_attempts = db.Column(db.Integer, nullable=False, default=0)
    created_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)

    worker_id = db.Column(db.String(64), nullable=True)
    worker_claimed_at = db.Column(db.DateTime, nullable=True)
    worker_heartbeat_at = db.Column(db.DateTime, nullable=True)

    last_result_json = db.Column(JSONB, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','processing','ready','failed','created','created_partial')",
            name="ck_receipt_modal_jobs_status",
        ),
        Index("idx_receipt_modal_jobs_user_created", "user_pk", "created_at"),
        Index("idx_receipt_modal_jobs_status_created", "status", "created_at"),
        Index("idx_receipt_modal_jobs_worker_heartbeat", "status", "worker_heartbeat_at"),
    )


class ReceiptModalJobItemRecord(db.Model):
    __tablename__ = "receipt_modal_job_items"

    id = db.Column(db.String(32), primary_key=True)
    job_id = db.Column(db.String(32), db.ForeignKey("receipt_modal_jobs.id"), nullable=False)
    user_pk = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    client_index = db.Column(db.Integer, nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    stored_path = db.Column(db.String(1024), nullable=True)

    status = db.Column(db.String(24), nullable=False, default="queued")
    error = db.Column(db.Text, nullable=True)

    occurred_on = db.Column(db.String(10), nullable=True)
    occurred_time = db.Column(db.String(5), nullable=True)
    amount_krw = db.Column(db.Integer, nullable=True)
    counterparty = db.Column(db.String(80), nullable=True)
    payment_item = db.Column(db.String(120), nullable=True)
    payment_method = db.Column(db.String(80), nullable=True)
    memo = db.Column(db.Text, nullable=True)
    usage = db.Column(db.String(16), nullable=False, default="unknown")
    warnings_json = db.Column(JSONB, nullable=True)
    created_transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','processing','ready','error','created')",
            name="ck_receipt_modal_job_items_status",
        ),
        CheckConstraint(
            "usage IN ('business','personal','unknown')",
            name="ck_receipt_modal_job_items_usage",
        ),
        Index("idx_receipt_modal_job_items_job_client", "job_id", "client_index"),
        Index("idx_receipt_modal_job_items_user_created", "user_pk", "created_at"),
        Index("idx_receipt_modal_job_items_status", "status"),
        UniqueConstraint("job_id", "client_index", name="uq_receipt_modal_job_items_job_client"),
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
        m = self._meta()
        m[key] = value
        self.custom_rates["_meta"] = m

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

    alias = db.Column(db.String(64), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_pk", "bank_code", "account_number", name="uq_user_bankacct"),
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


# ✅ 코드 호환: 어떤 파일은 Settings라는 이름을 import할 수도 있음
Settings = SafeToSpendSettings
