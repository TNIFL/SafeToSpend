"""add user plan column

Revision ID: f4e9d1c2a8b0
Revises: d3c1f4a9e2b7
Create Date: 2026-03-01 23:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4e9d1c2a8b0"
down_revision = "d3c1f4a9e2b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"))
        batch_op.create_check_constraint("ck_users_plan", "plan IN ('free','pro')")

    op.execute("ALTER TABLE users ALTER COLUMN plan DROP DEFAULT")


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("ck_users_plan", type_="check")
        batch_op.drop_column("plan")

