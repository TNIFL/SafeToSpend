"""add billing checkout intents domain

Revision ID: c7e1d9a4b2f0
Revises: b6a9c2d4e7f1
Create Date: 2026-03-10 01:20:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c7e1d9a4b2f0"
down_revision = "b6a9c2d4e7f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_checkout_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("intent_type", sa.String(length=32), nullable=False),
        sa.Column("target_plan_code", sa.String(length=16), nullable=True),
        sa.Column("addon_quantity", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="KRW"),
        sa.Column("amount_snapshot_krw", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "pricing_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("requires_billing_method", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("billing_method_id", sa.Integer(), nullable=True),
        sa.Column("related_subscription_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("resume_token", sa.String(length=128), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "intent_type IN ('initial_subscription','upgrade','addon_proration')",
            name="ck_billing_checkout_intents_type",
        ),
        sa.CheckConstraint(
            "target_plan_code IN ('free','basic','pro') OR target_plan_code IS NULL",
            name="ck_billing_checkout_intents_plan",
        ),
        sa.CheckConstraint(
            "addon_quantity IS NULL OR addon_quantity >= 0",
            name="ck_billing_checkout_intents_addon_nonneg",
        ),
        sa.CheckConstraint("currency IN ('KRW')", name="ck_billing_checkout_intents_currency"),
        sa.CheckConstraint("amount_snapshot_krw >= 0", name="ck_billing_checkout_intents_amount_nonneg"),
        sa.CheckConstraint(
            "status IN ('created','registration_required','ready_for_charge','charge_started','completed','failed','abandoned','canceled')",
            name="ck_billing_checkout_intents_status",
        ),
        sa.ForeignKeyConstraint(["billing_method_id"], ["billing_methods.id"]),
        sa.ForeignKeyConstraint(["related_subscription_id"], ["billing_subscriptions.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_token", name="uq_billing_checkout_intents_resume_token"),
        sa.UniqueConstraint("user_pk", "idempotency_key", name="uq_billing_checkout_intents_user_idempotency"),
    )
    op.create_index("ix_billing_checkout_intents_user_pk", "billing_checkout_intents", ["user_pk"], unique=False)
    op.create_index("ix_billing_checkout_intents_billing_method_id", "billing_checkout_intents", ["billing_method_id"], unique=False)
    op.create_index(
        "ix_billing_checkout_intents_related_subscription_id",
        "billing_checkout_intents",
        ["related_subscription_id"],
        unique=False,
    )
    op.create_index(
        "idx_billing_checkout_intents_user_status",
        "billing_checkout_intents",
        ["user_pk", "status"],
        unique=False,
    )
    op.create_index(
        "idx_billing_checkout_intents_user_requested",
        "billing_checkout_intents",
        ["user_pk", "requested_at"],
        unique=False,
    )
    op.create_index("idx_billing_checkout_intents_expires", "billing_checkout_intents", ["expires_at"], unique=False)


def downgrade():
    op.drop_index("idx_billing_checkout_intents_expires", table_name="billing_checkout_intents")
    op.drop_index("idx_billing_checkout_intents_user_requested", table_name="billing_checkout_intents")
    op.drop_index("idx_billing_checkout_intents_user_status", table_name="billing_checkout_intents")
    op.drop_index("ix_billing_checkout_intents_related_subscription_id", table_name="billing_checkout_intents")
    op.drop_index("ix_billing_checkout_intents_billing_method_id", table_name="billing_checkout_intents")
    op.drop_index("ix_billing_checkout_intents_user_pk", table_name="billing_checkout_intents")
    op.drop_table("billing_checkout_intents")
