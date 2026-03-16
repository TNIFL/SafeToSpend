"""add user plan entitlement fields

Revision ID: a1d8c3b5f701
Revises: f9b1c2d3e4f5
Create Date: 2026-03-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1d8c3b5f701"
down_revision = "f9b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_code", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("plan_status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("extra_account_slots", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("plan_updated_at", sa.DateTime(), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET plan_code = CASE
                WHEN lower(coalesce(plan, '')) = 'pro' THEN 'pro'
                ELSE 'free'
            END
            WHERE plan_code IS NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET plan_status = 'active'
            WHERE plan_status IS NULL OR trim(plan_status) = ''
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET extra_account_slots = 0
            WHERE extra_account_slots IS NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET plan_updated_at = CURRENT_TIMESTAMP
            WHERE plan_updated_at IS NULL
            """
        )
    )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("plan_code", existing_type=sa.String(length=16), nullable=False)
        batch_op.alter_column("plan_status", existing_type=sa.String(length=16), nullable=False)
        batch_op.alter_column("extra_account_slots", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("plan_updated_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.create_check_constraint(
            "ck_users_plan_code",
            "plan_code IN ('free','basic','pro')",
        )
        batch_op.create_check_constraint(
            "ck_users_plan_status",
            "plan_status IN ('active','inactive','canceled','past_due')",
        )
        batch_op.create_check_constraint(
            "ck_users_extra_account_slots_nonneg",
            "extra_account_slots >= 0",
        )


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("ck_users_extra_account_slots_nonneg", type_="check")
        batch_op.drop_constraint("ck_users_plan_status", type_="check")
        batch_op.drop_constraint("ck_users_plan_code", type_="check")
        batch_op.drop_column("plan_updated_at")
        batch_op.drop_column("extra_account_slots")
        batch_op.drop_column("plan_status")
        batch_op.drop_column("plan_code")
