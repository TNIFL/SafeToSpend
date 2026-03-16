"""add transaction review state

Revision ID: 7e4f1a2d9c6b
Revises: 9c2f4d8b7a11
Create Date: 2026-03-02 01:25:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7e4f1a2d9c6b"
down_revision = "9c2f4d8b7a11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("review_state", sa.String(length=16), nullable=False, server_default="todo"))
        batch_op.create_check_constraint("ck_transactions_review_state", "review_state IN ('todo','hold','done')")

    op.execute("ALTER TABLE transactions ALTER COLUMN review_state DROP DEFAULT")


def downgrade():
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_transactions_review_state", type_="check")
        batch_op.drop_column("review_state")
