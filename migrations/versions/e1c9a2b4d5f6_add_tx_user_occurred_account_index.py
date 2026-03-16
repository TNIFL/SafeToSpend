"""add composite transaction index for account scoped calendar queries

Revision ID: e1c9a2b4d5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-03-08 23:20:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1c9a2b4d5f6"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


INDEX_NAME = "idx_tx_user_occurred_account"
TABLE_NAME = "transactions"


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    for row in indexes or []:
        if str((row or {}).get("name") or "") == index_name:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    if _has_index(bind, TABLE_NAME, INDEX_NAME):
        return
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        ["user_pk", "occurred_at", "bank_account_id"],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    if not _has_index(bind, TABLE_NAME, INDEX_NAME):
        return
    op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

