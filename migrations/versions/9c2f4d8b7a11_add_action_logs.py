"""add action logs

Revision ID: 9c2f4d8b7a11
Revises: f4e9d1c2a8b0
Create Date: 2026-03-02 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "9c2f4d8b7a11"
down_revision = "f4e9d1c2a8b0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_reverted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('label_update','mark_unneeded','attach','bulk_update')",
            name="ck_action_logs_action_type",
        ),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("action_logs", schema=None) as batch_op:
        batch_op.create_index("idx_action_logs_user_created", ["user_pk", "created_at"], unique=False)
        batch_op.create_index("idx_action_logs_user_reverted", ["user_pk", "is_reverted"], unique=False)

    op.execute("ALTER TABLE action_logs ALTER COLUMN is_reverted DROP DEFAULT")


def downgrade():
    with op.batch_alter_table("action_logs", schema=None) as batch_op:
        batch_op.drop_index("idx_action_logs_user_reverted")
        batch_op.drop_index("idx_action_logs_user_created")
    op.drop_table("action_logs")
