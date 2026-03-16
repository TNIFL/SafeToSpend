"""add tax profiles

Revision ID: b21fcb5d2c41
Revises: 25cf30a63ddf
Create Date: 2026-03-01 02:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b21fcb5d2c41"
down_revision = "25cf30a63ddf"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tax_profiles",
        sa.Column("user_pk", sa.Integer(), nullable=False),
        sa.Column(
            "profile_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_pk"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_pk"),
    )
    op.execute("ALTER TABLE tax_profiles ALTER COLUMN profile_json DROP DEFAULT")


def downgrade():
    op.drop_table("tax_profiles")
