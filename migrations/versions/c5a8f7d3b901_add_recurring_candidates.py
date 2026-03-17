"""add recurring candidates

Revision ID: c5a8f7d3b901
Revises: 7e4f1a2d9c6b
Create Date: 2026-03-02 05:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c5a8f7d3b901"
down_revision = "7e4f1a2d9c6b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("counterparty", sa.String(length=255), nullable=False),
        sa.Column("amount_bucket", sa.Integer(), nullable=False),
        sa.Column("cadence", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("direction IN ('in','out')", name="ck_rc_direction"),
        sa.CheckConstraint("amount_bucket >= 0", name="ck_rc_amount_nonneg"),
        sa.CheckConstraint("cadence IN ('monthly')", name="ck_rc_cadence"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rc_confidence_range"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_pk",
            "direction",
            "counterparty",
            "amount_bucket",
            "cadence",
            name="uq_rc_user_key",
        ),
    )
    with op.batch_alter_table("recurring_candidates", schema=None) as batch_op:
        batch_op.create_index("idx_rc_user_conf", ["user_pk", "confidence"], unique=False)
        batch_op.create_index("idx_rc_user_seen", ["user_pk", "last_seen_at"], unique=False)


def downgrade():
    with op.batch_alter_table("recurring_candidates", schema=None) as batch_op:
        batch_op.drop_index("idx_rc_user_seen")
        batch_op.drop_index("idx_rc_user_conf")
    op.drop_table("recurring_candidates")

