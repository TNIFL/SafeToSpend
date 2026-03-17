"""add nhis bill history table

Revision ID: c9d4a1e7b2f0
Revises: b7c1f9d3e4a8
Create Date: 2026-03-03 23:50:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9d4a1e7b2f0"
down_revision = "b7c1f9d3e4a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nhis_bill_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("bill_year", sa.Integer(), nullable=False),
        sa.Column("bill_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_krw", sa.Integer(), nullable=True),
        sa.Column("health_only_krw", sa.Integer(), nullable=True),
        sa.Column("score_points", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("bill_year >= 2000 AND bill_year <= 2100", name="ck_nhis_bill_history_year"),
        sa.CheckConstraint("bill_month >= 0 AND bill_month <= 12", name="ck_nhis_bill_history_month"),
        sa.CheckConstraint("total_krw IS NULL OR total_krw >= 0", name="ck_nhis_bill_history_total_nonneg"),
        sa.CheckConstraint("health_only_krw IS NULL OR health_only_krw >= 0", name="ck_nhis_bill_history_health_nonneg"),
        sa.CheckConstraint("score_points IS NULL OR score_points >= 0", name="ck_nhis_bill_history_score_nonneg"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk", "bill_year", "bill_month", name="uq_nhis_bill_history_user_year_month"),
    )
    op.create_index("idx_nhis_bill_history_user_year", "nhis_bill_history", ["user_pk", "bill_year"], unique=False)
    op.create_index("idx_nhis_bill_history_user_updated", "nhis_bill_history", ["user_pk", "updated_at"], unique=False)
    op.execute("ALTER TABLE nhis_bill_history ALTER COLUMN bill_month DROP DEFAULT")


def downgrade() -> None:
    op.drop_index("idx_nhis_bill_history_user_updated", table_name="nhis_bill_history")
    op.drop_index("idx_nhis_bill_history_user_year", table_name="nhis_bill_history")
    op.drop_table("nhis_bill_history")
