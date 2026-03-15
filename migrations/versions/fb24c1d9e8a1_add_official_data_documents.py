"""add official data documents

Revision ID: fb24c1d9e8a1
Revises: fa13c7d9e2b4
Create Date: 2026-03-15 22:45:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "fb24c1d9e8a1"
down_revision = "fa13c7d9e2b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_data_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(length=24), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("file_name_original", sa.String(length=255), nullable=False),
        sa.Column("file_mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=48), nullable=False, server_default="official_data_parser_v1"),
        sa.Column("parse_status", sa.String(length=24), nullable=False, server_default="uploaded"),
        sa.Column("parse_error_code", sa.String(length=64), nullable=True),
        sa.Column("parse_error_detail", sa.Text(), nullable=True),
        sa.Column(
            "extracted_payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "extracted_key_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("document_issued_at", sa.DateTime(), nullable=True),
        sa.Column("document_period_start", sa.Date(), nullable=True),
        sa.Column("document_period_end", sa.Date(), nullable=True),
        sa.Column("verified_reference_date", sa.Date(), nullable=True),
        sa.Column("raw_file_storage_mode", sa.String(length=24), nullable=False, server_default="none"),
        sa.Column("raw_file_key", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("source_system IN ('hometax','nhis')", name="ck_official_data_source_system"),
        sa.CheckConstraint(
            "parse_status IN ('uploaded','parsed','needs_review','unsupported','failed')",
            name="ck_official_data_parse_status",
        ),
        sa.CheckConstraint(
            "raw_file_storage_mode IN ('none','optional_saved')",
            name="ck_official_data_raw_file_storage_mode",
        ),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("official_data_documents", schema=None) as batch_op:
        batch_op.create_index("idx_official_data_user_parse_status", ["user_pk", "parse_status"], unique=False)
        batch_op.create_index("idx_official_data_user_source_doc", ["user_pk", "source_system", "document_type"], unique=False)
        batch_op.create_index("idx_official_data_user_reference_date", ["user_pk", "verified_reference_date"], unique=False)
        batch_op.create_index("idx_official_data_user_created", ["user_pk", "created_at"], unique=False)


def downgrade():
    with op.batch_alter_table("official_data_documents", schema=None) as batch_op:
        batch_op.drop_index("idx_official_data_user_created")
        batch_op.drop_index("idx_official_data_user_reference_date")
        batch_op.drop_index("idx_official_data_user_source_doc")
        batch_op.drop_index("idx_official_data_user_parse_status")
    op.drop_table("official_data_documents")
