"""add receipt batches and items

Revision ID: d3c1f4a9e2b7
Revises: b21fcb5d2c41
Create Date: 2026-03-01 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d3c1f4a9e2b7"
down_revision = "b21fcb5d2c41"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("month_key", sa.String(length=7), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued','processing','done','done_with_errors')", name="ck_receipt_batches_status"),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("receipt_batches", schema=None) as batch_op:
        batch_op.create_index("idx_receipt_batches_user_created", ["user_pk", "created_at"], unique=False)
        batch_op.create_index("idx_receipt_batches_user_status", ["user_pk", "status"], unique=False)

    op.create_table(
        "receipt_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("file_key", sa.String(length=512), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("receipt_type", sa.String(length=24), nullable=True),
        sa.Column("parsed_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('uploaded','processing','done','failed')", name="ck_receipt_items_status"),
        sa.ForeignKeyConstraint(["batch_id"], ["receipt_batches.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("receipt_items", schema=None) as batch_op:
        batch_op.create_index("idx_receipt_items_batch", ["batch_id"], unique=False)
        batch_op.create_index("idx_receipt_items_user_status", ["user_pk", "status"], unique=False)
        batch_op.create_index("idx_receipt_items_user_sha", ["user_pk", "sha256"], unique=False)

    op.execute("ALTER TABLE receipt_batches ALTER COLUMN total_count DROP DEFAULT")
    op.execute("ALTER TABLE receipt_batches ALTER COLUMN done_count DROP DEFAULT")
    op.execute("ALTER TABLE receipt_batches ALTER COLUMN failed_count DROP DEFAULT")


def downgrade():
    with op.batch_alter_table("receipt_items", schema=None) as batch_op:
        batch_op.drop_index("idx_receipt_items_user_sha")
        batch_op.drop_index("idx_receipt_items_user_status")
        batch_op.drop_index("idx_receipt_items_batch")
    op.drop_table("receipt_items")

    with op.batch_alter_table("receipt_batches", schema=None) as batch_op:
        batch_op.drop_index("idx_receipt_batches_user_status")
        batch_op.drop_index("idx_receipt_batches_user_created")
    op.drop_table("receipt_batches")
