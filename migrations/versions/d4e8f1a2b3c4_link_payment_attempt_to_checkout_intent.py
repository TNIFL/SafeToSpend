"""link payment attempts to checkout intents

Revision ID: d4e8f1a2b3c4
Revises: c7e1d9a4b2f0
Create Date: 2026-03-10 01:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4e8f1a2b3c4"
down_revision = "c7e1d9a4b2f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("billing_payment_attempts", sa.Column("checkout_intent_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_billing_payment_attempts_checkout_intent_id",
        "billing_payment_attempts",
        "billing_checkout_intents",
        ["checkout_intent_id"],
        ["id"],
    )
    op.create_index(
        "ix_billing_payment_attempts_checkout_intent_id",
        "billing_payment_attempts",
        ["checkout_intent_id"],
        unique=False,
    )
    op.create_index(
        "idx_billing_payment_attempts_intent_status",
        "billing_payment_attempts",
        ["checkout_intent_id", "status"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_billing_payment_attempts_intent_status", table_name="billing_payment_attempts")
    op.drop_index("ix_billing_payment_attempts_checkout_intent_id", table_name="billing_payment_attempts")
    op.drop_constraint("fk_billing_payment_attempts_checkout_intent_id", "billing_payment_attempts", type_="foreignkey")
    op.drop_column("billing_payment_attempts", "checkout_intent_id")
