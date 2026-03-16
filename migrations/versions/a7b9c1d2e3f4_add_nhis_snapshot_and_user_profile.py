"""add nhis snapshot and user profile

Revision ID: a7b9c1d2e3f4
Revises: f0a1b2c3d4e5
Create Date: 2026-03-03 03:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a7b9c1d2e3f4"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nhis_rate_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("effective_year", sa.Integer(), nullable=False),
        sa.Column("health_insurance_rate", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("long_term_care_ratio_of_health", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("long_term_care_rate_optional", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("regional_point_value", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("property_basic_deduction_krw", sa.Integer(), nullable=False),
        sa.Column("car_premium_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("income_reference_rule", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sources_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("effective_year", name="uq_nhis_snapshot_year"),
    )
    op.create_index("idx_nhis_snapshot_year", "nhis_rate_snapshots", ["effective_year"], unique=False)
    op.create_index("idx_nhis_snapshot_active", "nhis_rate_snapshots", ["is_active", "effective_year"], unique=False)

    op.create_table(
        "nhis_user_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("member_type", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("target_month", sa.String(length=7), nullable=False, server_default=""),
        sa.Column("household_has_others", sa.Boolean(), nullable=True),
        sa.Column("annual_income_krw", sa.Integer(), nullable=True),
        sa.Column("salary_monthly_krw", sa.Integer(), nullable=True),
        sa.Column("non_salary_annual_income_krw", sa.Integer(), nullable=True),
        sa.Column("property_tax_base_total_krw", sa.Integer(), nullable=True),
        sa.Column("rent_deposit_krw", sa.Integer(), nullable=True),
        sa.Column("rent_monthly_krw", sa.Integer(), nullable=True),
        sa.Column("has_reduction_or_relief", sa.Boolean(), nullable=True),
        sa.Column("has_housing_loan_deduction", sa.Boolean(), nullable=True),
        sa.Column("last_bill_total_krw", sa.Integer(), nullable=True),
        sa.Column("last_bill_health_only_krw", sa.Integer(), nullable=True),
        sa.Column("last_bill_score_points", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "member_type IN ('regional','employee','dependent','unknown')",
            name="ck_nhis_user_profiles_member_type",
        ),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk"),
    )
    op.create_index("idx_nhis_user_profiles_user", "nhis_user_profiles", ["user_pk"], unique=False)
    op.create_index("idx_nhis_user_profiles_target_month", "nhis_user_profiles", ["target_month"], unique=False)

    op.execute("ALTER TABLE nhis_rate_snapshots ALTER COLUMN car_premium_enabled DROP DEFAULT")
    op.execute("ALTER TABLE nhis_rate_snapshots ALTER COLUMN income_reference_rule DROP DEFAULT")
    op.execute("ALTER TABLE nhis_rate_snapshots ALTER COLUMN sources_json DROP DEFAULT")
    op.execute("ALTER TABLE nhis_rate_snapshots ALTER COLUMN is_active DROP DEFAULT")
    op.execute("ALTER TABLE nhis_user_profiles ALTER COLUMN member_type DROP DEFAULT")
    op.execute("ALTER TABLE nhis_user_profiles ALTER COLUMN target_month DROP DEFAULT")


def downgrade() -> None:
    op.drop_index("idx_nhis_user_profiles_target_month", table_name="nhis_user_profiles")
    op.drop_index("idx_nhis_user_profiles_user", table_name="nhis_user_profiles")
    op.drop_table("nhis_user_profiles")

    op.drop_index("idx_nhis_snapshot_active", table_name="nhis_rate_snapshots")
    op.drop_index("idx_nhis_snapshot_year", table_name="nhis_rate_snapshots")
    op.drop_table("nhis_rate_snapshots")

