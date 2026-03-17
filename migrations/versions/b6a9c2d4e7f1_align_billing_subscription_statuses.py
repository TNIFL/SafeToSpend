"""align billing subscription status check with state machine

Revision ID: b6a9c2d4e7f1
Revises: b4c7d8e9f0a1
Create Date: 2026-03-09 11:52:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b6a9c2d4e7f1"
down_revision = "b4c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_billing_subscriptions_status", "billing_subscriptions", type_="check")
    op.create_check_constraint(
        "ck_billing_subscriptions_status",
        "billing_subscriptions",
        "status IN ('pending_activation','active','grace_started','cancel_requested','canceled','past_due')",
    )


def downgrade():
    op.drop_constraint("ck_billing_subscriptions_status", "billing_subscriptions", type_="check")
    op.create_check_constraint(
        "ck_billing_subscriptions_status",
        "billing_subscriptions",
        "status IN ('pending_activation','active','cancel_requested','canceled','past_due')",
    )
