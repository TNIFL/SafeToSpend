"""add legal document metadata

Revision ID: 0bb0f6ee8e8c
Revises: 8b4d4e4b63b6
Create Date: 2026-03-21 10:00:00.000000
"""

from __future__ import annotations

import datetime as dt

from alembic import op
import sqlalchemy as sa


revision = "0bb0f6ee8e8c"
down_revision = "8b4d4e4b63b6"
branch_labels = None
depends_on = None


legal_document_metadata = sa.table(
    "legal_document_metadata",
    sa.column("document_type", sa.String(length=64)),
    sa.column("version", sa.String(length=32)),
    sa.column("display_name", sa.String(length=120)),
    sa.column("status", sa.String(length=16)),
    sa.column("effective_at", sa.DateTime()),
    sa.column("requires_reconsent", sa.Boolean()),
    sa.column("summary", sa.Text()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade():
    op.create_table(
        "legal_document_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("requires_reconsent", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_legal_document_metadata_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_type", "version", name="uq_legal_document_type_version"),
    )
    op.create_index(
        "idx_legal_document_type_status",
        "legal_document_metadata",
        ["document_type", "status"],
        unique=False,
    )
    op.create_index(
        "uq_legal_document_active_per_type",
        "legal_document_metadata",
        ["document_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    now = dt.datetime(2026, 3, 21, 0, 0, 0)
    op.bulk_insert(
        legal_document_metadata,
        [
            {
                "document_type": "terms_of_service",
                "version": "2026-03-draft-1",
                "display_name": "이용약관",
                "status": "active",
                "effective_at": now,
                "requires_reconsent": False,
                "summary": "현재 회원가입 기준 이용약관 초안입니다.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "document_type": "privacy_policy",
                "version": "2026-03-draft-1",
                "display_name": "개인정보처리방침",
                "status": "active",
                "effective_at": now,
                "requires_reconsent": False,
                "summary": "현재 회원가입 기준 개인정보처리방침 초안입니다.",
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade():
    op.drop_index("uq_legal_document_active_per_type", table_name="legal_document_metadata")
    op.drop_index("idx_legal_document_type_status", table_name="legal_document_metadata")
    op.drop_table("legal_document_metadata")
