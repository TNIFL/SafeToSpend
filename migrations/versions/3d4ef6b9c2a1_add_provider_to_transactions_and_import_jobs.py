"""add provider to transactions and import jobs

Revision ID: 3d4ef6b9c2a1
Revises: 0bb0f6ee8e8c
Create Date: 2026-03-22 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "3d4ef6b9c2a1"
down_revision = "0bb0f6ee8e8c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_jobs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("transactions", sa.Column("provider", sa.String(length=32), nullable=True))

    op.create_index(
        "idx_import_jobs_user_source_provider",
        "import_jobs",
        ["user_pk", "source", "provider"],
        unique=False,
    )
    op.create_index(
        "idx_tx_user_source_provider",
        "transactions",
        ["user_pk", "source", "provider"],
        unique=False,
    )

    op.execute(
        """
        UPDATE import_jobs
        SET provider = 'popbill'
        WHERE source = 'popbill' AND provider IS NULL
        """
    )
    op.execute(
        """
        UPDATE transactions
        SET provider = 'popbill'
        WHERE source = 'popbill' AND provider IS NULL
        """
    )


def downgrade():
    op.drop_index("idx_tx_user_source_provider", table_name="transactions")
    op.drop_index("idx_import_jobs_user_source_provider", table_name="import_jobs")
    op.drop_column("transactions", "provider")
    op.drop_column("import_jobs", "provider")

