"""add user bank accounts and transaction/account links

Revision ID: d1e2f3a4b5c6
Revises: c9d4a1e7b2f0
Create Date: 2026-03-08 06:05:00.000000

"""
from __future__ import annotations

import hashlib
import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c9d4a1e7b2f0"
branch_labels = None
depends_on = None


_DIGIT_RE = re.compile(r"[^0-9]")


def _normalize_digits(raw: str | None) -> str:
    return _DIGIT_RE.sub("", str(raw or ""))


def _fingerprint(digits: str) -> str | None:
    if not digits:
        return None
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def _last4(digits: str) -> str | None:
    if len(digits) < 4:
        return None
    return digits[-4:]


def upgrade():
    op.create_table(
        "user_bank_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column("bank_code", sa.String(length=4), nullable=True),
        sa.Column("account_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("account_last4", sa.String(length=4), nullable=True),
        sa.Column("alias", sa.String(length=64), nullable=True),
        sa.Column("color_hex", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_pk", "account_fingerprint", name="uq_user_bank_account_fingerprint"),
    )
    op.create_index("ix_user_bank_accounts_user_pk", "user_bank_accounts", ["user_pk"], unique=False)
    op.create_index(
        "idx_user_bank_accounts_user_created",
        "user_bank_accounts",
        ["user_pk", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bank_account_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_transactions_bank_account_id"), ["bank_account_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_transactions_bank_account_id_user_bank_accounts",
            "user_bank_accounts",
            ["bank_account_id"],
            ["id"],
        )

    with op.batch_alter_table("bank_account_links", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bank_account_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_bank_account_links_bank_account_id"), ["bank_account_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_bank_account_links_bank_account_id_user_bank_accounts",
            "user_bank_accounts",
            ["bank_account_id"],
            ["id"],
        )

    conn = op.get_bind()
    bank_links = sa.table(
        "bank_account_links",
        sa.column("id", sa.Integer),
        sa.column("user_pk", sa.Integer),
        sa.column("bank_code", sa.String),
        sa.column("account_number", sa.String),
        sa.column("alias", sa.String),
        sa.column("bank_account_id", sa.Integer),
    )
    user_bank_accounts = sa.table(
        "user_bank_accounts",
        sa.column("id", sa.Integer),
        sa.column("user_pk", sa.Integer),
        sa.column("bank_code", sa.String),
        sa.column("account_fingerprint", sa.String),
        sa.column("account_last4", sa.String),
        sa.column("alias", sa.String),
        sa.column("color_hex", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    rows = conn.execute(
        sa.select(
            bank_links.c.id,
            bank_links.c.user_pk,
            bank_links.c.bank_code,
            bank_links.c.account_number,
            bank_links.c.alias,
        )
    ).mappings()

    for row in rows:
        user_pk = int(row["user_pk"])
        digits = _normalize_digits(row["account_number"])
        fp = _fingerprint(digits)
        last4 = _last4(digits)
        account_id = None

        if fp:
            account_id = conn.execute(
                sa.select(user_bank_accounts.c.id)
                .where(user_bank_accounts.c.user_pk == user_pk)
                .where(user_bank_accounts.c.account_fingerprint == fp)
                .limit(1)
            ).scalar()

        if not account_id:
            alias = (row.get("alias") or "").strip() or "연동 계좌"
            inserted = conn.execute(
                user_bank_accounts.insert().values(
                    user_pk=user_pk,
                    bank_code=(row.get("bank_code") or None),
                    account_fingerprint=fp,
                    account_last4=last4,
                    alias=alias,
                    color_hex=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            account_id = int(inserted.inserted_primary_key[0])

        conn.execute(
            bank_links.update()
            .where(bank_links.c.id == int(row["id"]))
            .values(bank_account_id=account_id)
        )


def downgrade():
    with op.batch_alter_table("bank_account_links", schema=None) as batch_op:
        batch_op.drop_constraint("fk_bank_account_links_bank_account_id_user_bank_accounts", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_bank_account_links_bank_account_id"))
        batch_op.drop_column("bank_account_id")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_transactions_bank_account_id_user_bank_accounts", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_transactions_bank_account_id"))
        batch_op.drop_column("bank_account_id")

    op.drop_index("idx_user_bank_accounts_user_created", table_name="user_bank_accounts")
    op.drop_index("ix_user_bank_accounts_user_pk", table_name="user_bank_accounts")
    op.drop_table("user_bank_accounts")
