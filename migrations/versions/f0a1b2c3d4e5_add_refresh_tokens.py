"""add refresh tokens

Revision ID: f0a1b2c3d4e5
Revises: e8a4c2b1d9f0
Create Date: 2026-03-02 23:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f0a1b2c3d4e5"
down_revision = "e8a4c2b1d9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("replaced_by_id", sa.Integer(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["replaced_by_id"], ["refresh_tokens.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_refresh_tokens_user_expires", "refresh_tokens", ["user_pk", "expires_at"], unique=False)
    op.create_index("idx_refresh_tokens_user_revoked", "refresh_tokens", ["user_pk", "revoked_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_refresh_tokens_user_revoked", table_name="refresh_tokens")
    op.drop_index("idx_refresh_tokens_user_expires", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

