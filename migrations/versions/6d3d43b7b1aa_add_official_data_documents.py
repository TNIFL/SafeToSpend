"""add official_data_documents

Revision ID: 6d3d43b7b1aa
Revises: 4f534039d904
Create Date: 2026-03-17 17:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6d3d43b7b1aa"
down_revision = "4f534039d904"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_data_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=True),
        sa.Column("source_authority", sa.String(length=120), nullable=True),
        sa.Column("raw_file_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=True),
        sa.Column("parse_status", sa.String(length=24), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("structure_validation_status", sa.String(length=24), nullable=False),
        sa.Column("trust_grade", sa.String(length=8), nullable=False),
        sa.Column("extracted_key_summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "parse_status IN ('parsed','needs_review','unsupported','failed')",
            name="ck_official_data_parse_status",
        ),
        sa.CheckConstraint(
            "verification_status IN ('not_verified','verified','verification_failed')",
            name="ck_official_data_verification_status",
        ),
        sa.CheckConstraint(
            "structure_validation_status IN ('passed','needs_review','unsupported','failed','unknown')",
            name="ck_official_data_structure_validation_status",
        ),
        sa.CheckConstraint("trust_grade IN ('A','B','C','D')", name="ck_official_data_trust_grade"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_official_data_user_created", "official_data_documents", ["user_pk", "created_at"], unique=False)
    op.create_index("idx_official_data_user_parse", "official_data_documents", ["user_pk", "parse_status"], unique=False)
    op.create_index("idx_official_data_user_reference", "official_data_documents", ["user_pk", "reference_date"], unique=False)


def downgrade():
    op.drop_index("idx_official_data_user_reference", table_name="official_data_documents")
    op.drop_index("idx_official_data_user_parse", table_name="official_data_documents")
    op.drop_index("idx_official_data_user_created", table_name="official_data_documents")
    op.drop_table("official_data_documents")
