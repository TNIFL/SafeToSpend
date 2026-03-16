"""add popbill link balance fields

Revision ID: f9b1c2d3e4f5
Revises: e1c9a2b4d5f6
Create Date: 2026-03-08 23:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f9b1c2d3e4f5"
down_revision = "e1c9a2b4d5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bank_account_links", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_balance_krw", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_balance_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("bank_account_links", schema=None) as batch_op:
        batch_op.drop_column("last_balance_checked_at")
        batch_op.drop_column("last_balance_krw")
