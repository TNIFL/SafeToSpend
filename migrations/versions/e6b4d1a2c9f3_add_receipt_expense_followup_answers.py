"""add receipt expense followup answers

Revision ID: e6b4d1a2c9f3
Revises: d4e8f1a2b3c4
Create Date: 2026-03-15 12:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6b4d1a2c9f3"
down_revision = "d4e8f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_expense_followup_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("evidence_item_id", sa.Integer(), nullable=True),
        sa.Column("question_key", sa.String(length=64), nullable=False),
        sa.Column("answer_value", sa.String(length=64), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=False),
        sa.Column("answered_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["answered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["evidence_item_id"], ["evidence_items.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_pk",
            "transaction_id",
            "question_key",
            name="uq_receipt_expense_followup_user_tx_question",
        ),
    )
    with op.batch_alter_table("receipt_expense_followup_answers", schema=None) as batch_op:
        batch_op.create_index("idx_receipt_followup_user_tx", ["user_pk", "transaction_id"], unique=False)
        batch_op.create_index("idx_receipt_followup_evidence", ["evidence_item_id"], unique=False)


def downgrade():
    with op.batch_alter_table("receipt_expense_followup_answers", schema=None) as batch_op:
        batch_op.drop_index("idx_receipt_followup_evidence")
        batch_op.drop_index("idx_receipt_followup_user_tx")
    op.drop_table("receipt_expense_followup_answers")
