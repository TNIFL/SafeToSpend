"""add user consent agreements

Revision ID: 8b4d4e4b63b6
Revises: 7c6e2f1d9a4b
Create Date: 2026-03-20 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8b4d4e4b63b6"
down_revision = "7c6e2f1d9a4b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_consent_agreements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("document_version", sa.String(length=32), nullable=False),
        sa.Column("agreed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk", "document_type", "document_version", name="uq_user_consent_doc_version"),
    )
    op.create_index(
        "idx_user_consent_user_agreed",
        "user_consent_agreements",
        ["user_pk", "agreed_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_consent_doc_version",
        "user_consent_agreements",
        ["document_type", "document_version"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_user_consent_doc_version", table_name="user_consent_agreements")
    op.drop_index("idx_user_consent_user_agreed", table_name="user_consent_agreements")
    op.drop_table("user_consent_agreements")
