"""add explicit user admin flag

Revision ID: 16d4b9a7c2f1
Revises: fb24c1d9e8a1
Create Date: 2026-03-16 10:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "16d4b9a7c2f1"
down_revision = "8fd1c2b3a4e5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_admin", sa.Boolean(), nullable=True, server_default=sa.false()))

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE users SET is_admin = FALSE WHERE is_admin IS NULL"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("is_admin", existing_type=sa.Boolean(), nullable=False, server_default=sa.false())


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_admin")
