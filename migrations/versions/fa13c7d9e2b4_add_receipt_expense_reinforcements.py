"""add receipt expense reinforcements

Revision ID: fa13c7d9e2b4
Revises: e6b4d1a2c9f3
Create Date: 2026-03-15 15:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fa13c7d9e2b4"
down_revision = "e6b4d1a2c9f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_expense_reinforcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("evidence_item_id", sa.Integer(), nullable=True),
        sa.Column("business_context_note", sa.Text(), nullable=True),
        sa.Column("attendee_names", sa.Text(), nullable=True),
        sa.Column("client_or_counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("ceremonial_relation_note", sa.Text(), nullable=True),
        sa.Column("asset_usage_note", sa.Text(), nullable=True),
        sa.Column("weekend_or_late_night_note", sa.Text(), nullable=True),
        sa.Column("supporting_file_key", sa.String(length=512), nullable=True),
        sa.Column("supporting_file_name", sa.String(length=255), nullable=True),
        sa.Column("supporting_file_mime_type", sa.String(length=120), nullable=True),
        sa.Column("supporting_file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("supporting_file_uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_item_id"], ["evidence_items.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_pk",
            "transaction_id",
            name="uq_receipt_expense_reinforcement_user_tx",
        ),
    )
    with op.batch_alter_table("receipt_expense_reinforcements", schema=None) as batch_op:
        batch_op.create_index("idx_receipt_reinforcement_user_tx", ["user_pk", "transaction_id"], unique=False)
        batch_op.create_index("idx_receipt_reinforcement_evidence", ["evidence_item_id"], unique=False)


def downgrade():
    with op.batch_alter_table("receipt_expense_reinforcements", schema=None) as batch_op:
        batch_op.drop_index("idx_receipt_reinforcement_evidence")
        batch_op.drop_index("idx_receipt_reinforcement_user_tx")
    op.drop_table("receipt_expense_reinforcements")
