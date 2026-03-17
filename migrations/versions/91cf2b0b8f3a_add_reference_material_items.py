"""add reference_material_items

Revision ID: 91cf2b0b8f3a
Revises: 6d3d43b7b1aa
Create Date: 2026-03-18 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "91cf2b0b8f3a"
down_revision = "6d3d43b7b1aa"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reference_material_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("material_kind", sa.String(length=24), nullable=False),
        sa.Column("raw_file_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "material_kind IN ('reference','note_attachment')",
            name="ck_reference_material_kind",
        ),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_reference_material_user_created",
        "reference_material_items",
        ["user_pk", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_reference_material_user_kind",
        "reference_material_items",
        ["user_pk", "material_kind"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_reference_material_user_kind", table_name="reference_material_items")
    op.drop_index("idx_reference_material_user_created", table_name="reference_material_items")
    op.drop_table("reference_material_items")
