"""add billing domain source-of-truth tables

Revision ID: b4c7d8e9f0a1
Revises: a1d8c3b5f701
Create Date: 2026-03-09 09:15:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b4c7d8e9f0a1"
down_revision = "a1d8c3b5f701"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("customer_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','inactive')", name="ck_billing_customers_status"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "customer_key", name="uq_billing_customer_provider_key"),
        sa.UniqueConstraint("user_pk", "provider", name="uq_billing_customer_user_provider"),
    )
    op.create_index("ix_billing_customers_user_pk", "billing_customers", ["user_pk"], unique=False)
    op.create_index("idx_billing_customers_user_provider", "billing_customers", ["user_pk", "provider"], unique=False)

    op.create_table(
        "billing_methods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("billing_customer_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("method_type", sa.String(length=24), nullable=False, server_default="card"),
        sa.Column("billing_key_enc", sa.Text(), nullable=False),
        sa.Column("billing_key_hash", sa.String(length=64), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("method_type IN ('card')", name="ck_billing_methods_type"),
        sa.CheckConstraint("status IN ('active','revoked','inactive')", name="ck_billing_methods_status"),
        sa.ForeignKeyConstraint(["billing_customer_id"], ["billing_customers.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "billing_key_hash", name="uq_billing_methods_provider_key_hash"),
    )
    op.create_index("ix_billing_methods_user_pk", "billing_methods", ["user_pk"], unique=False)
    op.create_index("ix_billing_methods_billing_customer_id", "billing_methods", ["billing_customer_id"], unique=False)
    op.create_index("idx_billing_methods_user_status", "billing_methods", ["user_pk", "status"], unique=False)

    op.create_table(
        "billing_method_registration_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("billing_customer_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("customer_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="registration_started"),
        sa.Column("fail_code", sa.String(length=64), nullable=True),
        sa.Column("fail_message_norm", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('registration_started','billing_key_issued','failed','canceled')",
            name="ck_billing_reg_attempts_status",
        ),
        sa.ForeignKeyConstraint(["billing_customer_id"], ["billing_customers.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_billing_reg_attempts_order_id"),
    )
    op.create_index(
        "ix_billing_method_registration_attempts_user_pk",
        "billing_method_registration_attempts",
        ["user_pk"],
        unique=False,
    )
    op.create_index(
        "ix_billing_method_registration_attempts_billing_customer_id",
        "billing_method_registration_attempts",
        ["billing_customer_id"],
        unique=False,
    )
    op.create_index(
        "idx_billing_reg_attempts_user_status",
        "billing_method_registration_attempts",
        ["user_pk", "status"],
        unique=False,
    )

    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("billing_customer_id", sa.Integer(), nullable=False),
        sa.Column("billing_method_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_activation"),
        sa.Column("billing_anchor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_billing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_activation','active','cancel_requested','canceled','past_due')",
            name="ck_billing_subscriptions_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_billing_subscriptions_retry_nonneg"),
        sa.ForeignKeyConstraint(["billing_customer_id"], ["billing_customers.id"]),
        sa.ForeignKeyConstraint(["billing_method_id"], ["billing_methods.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_subscriptions_user_pk", "billing_subscriptions", ["user_pk"], unique=False)
    op.create_index(
        "ix_billing_subscriptions_billing_customer_id",
        "billing_subscriptions",
        ["billing_customer_id"],
        unique=False,
    )
    op.create_index("ix_billing_subscriptions_billing_method_id", "billing_subscriptions", ["billing_method_id"], unique=False)
    op.create_index("idx_billing_subscriptions_user_status", "billing_subscriptions", ["user_pk", "status"], unique=False)
    op.create_index("idx_billing_subscriptions_next_billing", "billing_subscriptions", ["next_billing_at"], unique=False)

    op.create_table(
        "billing_subscription_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_code", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price_krw", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_krw", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_krw >= 0", name="ck_billing_subscription_items_amount_nonneg"),
        sa.CheckConstraint("quantity >= 0", name="ck_billing_subscription_items_qty_nonneg"),
        sa.CheckConstraint(
            "item_type IN ('plan_base','addon_account_slot')",
            name="ck_billing_subscription_items_type",
        ),
        sa.CheckConstraint("status IN ('active','pending','removed')", name="ck_billing_subscription_items_status"),
        sa.CheckConstraint("unit_price_krw >= 0", name="ck_billing_subscription_items_unit_nonneg"),
        sa.ForeignKeyConstraint(["subscription_id"], ["billing_subscriptions.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_subscription_items_subscription_id", "billing_subscription_items", ["subscription_id"], unique=False)
    op.create_index("ix_billing_subscription_items_user_pk", "billing_subscription_items", ["user_pk"], unique=False)
    op.create_index(
        "idx_billing_subscription_items_sub_status",
        "billing_subscription_items",
        ["subscription_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_billing_subscription_items_user_active",
        "billing_subscription_items",
        ["user_pk", "status"],
        unique=False,
    )

    op.create_table(
        "billing_payment_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("attempt_type", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("payment_key", sa.String(length=128), nullable=True),
        sa.Column("amount_krw", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="KRW"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="charge_started"),
        sa.Column("fail_code", sa.String(length=64), nullable=True),
        sa.Column("fail_message_norm", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_krw >= 0", name="ck_billing_payment_attempts_amount_nonneg"),
        sa.CheckConstraint(
            "attempt_type IN ('initial','recurring','upgrade_full_charge','addon_proration','retry')",
            name="ck_billing_payment_attempts_type",
        ),
        sa.CheckConstraint(
            "status IN ('charge_started','authorized','failed','reconciled','reconcile_needed','canceled')",
            name="ck_billing_payment_attempts_status",
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["billing_subscriptions.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_billing_payment_attempts_order_id"),
        sa.UniqueConstraint("provider", "payment_key", name="uq_billing_payment_attempts_provider_payment_key"),
    )
    op.create_index("ix_billing_payment_attempts_user_pk", "billing_payment_attempts", ["user_pk"], unique=False)
    op.create_index("ix_billing_payment_attempts_subscription_id", "billing_payment_attempts", ["subscription_id"], unique=False)
    op.create_index(
        "idx_billing_payment_attempts_user_status",
        "billing_payment_attempts",
        ["user_pk", "status"],
        unique=False,
    )
    op.create_index(
        "idx_billing_payment_attempts_sub_status",
        "billing_payment_attempts",
        ["subscription_id", "status"],
        unique=False,
    )

    op.create_table(
        "billing_payment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="toss"),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("transmission_id", sa.String(length=128), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("related_order_id", sa.String(length=64), nullable=True),
        sa.Column("related_payment_key", sa.String(length=128), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('received','validated','applied','ignored_duplicate','failed')",
            name="ck_billing_payment_events_status",
        ),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_hash", name="uq_billing_payment_events_provider_hash"),
        sa.UniqueConstraint("provider", "transmission_id", name="uq_billing_payment_events_provider_tx"),
    )
    op.create_index("ix_billing_payment_events_user_pk", "billing_payment_events", ["user_pk"], unique=False)
    op.create_index("idx_billing_payment_events_order", "billing_payment_events", ["related_order_id"], unique=False)
    op.create_index(
        "idx_billing_payment_events_payment_key",
        "billing_payment_events",
        ["related_payment_key"],
        unique=False,
    )

    op.create_table(
        "entitlement_change_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk", "source_type", "source_id", name="uq_entitlement_change_logs_source"),
    )
    op.create_index("ix_entitlement_change_logs_user_pk", "entitlement_change_logs", ["user_pk"], unique=False)
    op.create_index(
        "idx_entitlement_change_logs_user_applied",
        "entitlement_change_logs",
        ["user_pk", "applied_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_entitlement_change_logs_user_applied", table_name="entitlement_change_logs")
    op.drop_index("ix_entitlement_change_logs_user_pk", table_name="entitlement_change_logs")
    op.drop_table("entitlement_change_logs")

    op.drop_index("idx_billing_payment_events_payment_key", table_name="billing_payment_events")
    op.drop_index("idx_billing_payment_events_order", table_name="billing_payment_events")
    op.drop_index("ix_billing_payment_events_user_pk", table_name="billing_payment_events")
    op.drop_table("billing_payment_events")

    op.drop_index("idx_billing_payment_attempts_sub_status", table_name="billing_payment_attempts")
    op.drop_index("idx_billing_payment_attempts_user_status", table_name="billing_payment_attempts")
    op.drop_index("ix_billing_payment_attempts_subscription_id", table_name="billing_payment_attempts")
    op.drop_index("ix_billing_payment_attempts_user_pk", table_name="billing_payment_attempts")
    op.drop_table("billing_payment_attempts")

    op.drop_index("idx_billing_subscription_items_user_active", table_name="billing_subscription_items")
    op.drop_index("idx_billing_subscription_items_sub_status", table_name="billing_subscription_items")
    op.drop_index("ix_billing_subscription_items_user_pk", table_name="billing_subscription_items")
    op.drop_index("ix_billing_subscription_items_subscription_id", table_name="billing_subscription_items")
    op.drop_table("billing_subscription_items")

    op.drop_index("idx_billing_subscriptions_next_billing", table_name="billing_subscriptions")
    op.drop_index("idx_billing_subscriptions_user_status", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_billing_method_id", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_billing_customer_id", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_user_pk", table_name="billing_subscriptions")
    op.drop_table("billing_subscriptions")

    op.drop_index("idx_billing_reg_attempts_user_status", table_name="billing_method_registration_attempts")
    op.drop_index(
        "ix_billing_method_registration_attempts_billing_customer_id",
        table_name="billing_method_registration_attempts",
    )
    op.drop_index("ix_billing_method_registration_attempts_user_pk", table_name="billing_method_registration_attempts")
    op.drop_table("billing_method_registration_attempts")

    op.drop_index("idx_billing_methods_user_status", table_name="billing_methods")
    op.drop_index("ix_billing_methods_billing_customer_id", table_name="billing_methods")
    op.drop_index("ix_billing_methods_user_pk", table_name="billing_methods")
    op.drop_table("billing_methods")

    op.drop_index("idx_billing_customers_user_provider", table_name="billing_customers")
    op.drop_index("ix_billing_customers_user_pk", table_name="billing_customers")
    op.drop_table("billing_customers")
