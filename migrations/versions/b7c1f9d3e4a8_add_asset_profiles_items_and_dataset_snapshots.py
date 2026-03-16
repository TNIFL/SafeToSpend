"""add asset profiles, items, and dataset snapshots

Revision ID: b7c1f9d3e4a8
Revises: a7b9c1d2e3f4
Create Date: 2026-03-03 11:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b7c1f9d3e4a8"
down_revision = "a7b9c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("household_has_others", sa.Boolean(), nullable=True),
        sa.Column("dependents_count", sa.Integer(), nullable=True),
        sa.Column("other_income_types_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("other_income_annual_krw", sa.Integer(), nullable=True),
        sa.Column("quiz_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("housing_mode", sa.String(length=16), nullable=True),
        sa.Column("has_car", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("dependents_count IS NULL OR dependents_count >= 0", name="ck_asset_profiles_dependents_nonneg"),
        sa.CheckConstraint("other_income_annual_krw IS NULL OR other_income_annual_krw >= 0", name="ck_asset_profiles_other_income_nonneg"),
        sa.CheckConstraint("housing_mode IS NULL OR housing_mode IN ('own','rent','jeonse','none','unknown')", name="ck_asset_profiles_housing_mode"),
        sa.CheckConstraint("quiz_step >= 1 AND quiz_step <= 6", name="ck_asset_profiles_quiz_step"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk"),
    )
    op.create_index("idx_asset_profiles_user", "asset_profiles", ["user_pk"], unique=False)
    op.create_index("idx_asset_profiles_completed", "asset_profiles", ["completed_at"], unique=False)

    op.create_table(
        "asset_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("basis_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("user_override_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('car','home','rent','deposit','other')", name="ck_asset_items_kind"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_asset_items_user_kind", "asset_items", ["user_pk", "kind"], unique=False)
    op.create_index("idx_asset_items_user_updated", "asset_items", ["user_pk", "updated_at"], unique=False)

    op.create_table(
        "asset_dataset_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_key", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("version_year", sa.Integer(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_key", "version_year", name="uq_asset_dataset_key_year"),
    )
    op.create_index("idx_asset_dataset_key_active", "asset_dataset_snapshots", ["dataset_key", "is_active"], unique=False)
    op.create_index("idx_asset_dataset_fetched", "asset_dataset_snapshots", ["fetched_at"], unique=False)

    op.execute("ALTER TABLE asset_profiles ALTER COLUMN other_income_types_json DROP DEFAULT")
    op.execute("ALTER TABLE asset_profiles ALTER COLUMN quiz_step DROP DEFAULT")
    op.execute("ALTER TABLE asset_items ALTER COLUMN input_json DROP DEFAULT")
    op.execute("ALTER TABLE asset_items ALTER COLUMN estimated_json DROP DEFAULT")
    op.execute("ALTER TABLE asset_items ALTER COLUMN basis_json DROP DEFAULT")
    op.execute("ALTER TABLE asset_dataset_snapshots ALTER COLUMN payload_json DROP DEFAULT")
    op.execute("ALTER TABLE asset_dataset_snapshots ALTER COLUMN is_active DROP DEFAULT")


def downgrade() -> None:
    op.drop_index("idx_asset_dataset_fetched", table_name="asset_dataset_snapshots")
    op.drop_index("idx_asset_dataset_key_active", table_name="asset_dataset_snapshots")
    op.drop_table("asset_dataset_snapshots")

    op.drop_index("idx_asset_items_user_updated", table_name="asset_items")
    op.drop_index("idx_asset_items_user_kind", table_name="asset_items")
    op.drop_table("asset_items")

    op.drop_index("idx_asset_profiles_completed", table_name="asset_profiles")
    op.drop_index("idx_asset_profiles_user", table_name="asset_profiles")
    op.drop_table("asset_profiles")
