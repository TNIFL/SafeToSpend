"""add inquiries

Revision ID: e8a4c2b1d9f0
Revises: c5a8f7d3b901
Create Date: 2026-03-02 22:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e8a4c2b1d9f0"
down_revision = "c5a8f7d3b901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inquiries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("admin_reply", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.Column("last_viewed_by_user_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('open','answered','closed')", name="ck_inquiries_status"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_inquiries_user_created", "inquiries", ["user_pk", "created_at"], unique=False)
    op.create_index("idx_inquiries_status_created", "inquiries", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_inquiries_status_created", table_name="inquiries")
    op.drop_index("idx_inquiries_user_created", table_name="inquiries")
    op.drop_table("inquiries")
