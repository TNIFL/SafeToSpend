"""add official data trust fields and identifier guards

Revision ID: 8fd1c2b3a4e5
Revises: fb24c1d9e8a1
Create Date: 2026-03-16 18:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8fd1c2b3a4e5"
down_revision = "fb24c1d9e8a1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("official_data_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trust_grade", sa.String(length=1), nullable=True))
        batch_op.add_column(sa.Column("trust_grade_label", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("trust_scope_label", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column(
                "structure_validation_status",
                sa.String(length=24),
                nullable=False,
                server_default="not_applicable",
            )
        )
        batch_op.add_column(sa.Column("verification_source", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "verification_status",
                sa.String(length=24),
                nullable=False,
                server_default="none",
            )
        )
        batch_op.add_column(sa.Column("verification_checked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("verification_reference_masked", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "user_modified_flag",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "sensitive_data_redacted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
        batch_op.create_check_constraint(
            "ck_official_data_trust_grade",
            "trust_grade IS NULL OR trust_grade IN ('A','B','C','D')",
        )
        batch_op.create_check_constraint(
            "ck_official_data_verification_status",
            "verification_status IN ('none','pending','succeeded','failed','not_applicable')",
        )
        batch_op.create_check_constraint(
            "ck_official_data_structure_validation_status",
            "structure_validation_status IN ('passed','failed','partial','not_applicable')",
        )
        batch_op.create_index("idx_official_data_user_trust_grade", ["user_pk", "trust_grade"], unique=False)
        batch_op.create_index(
            "idx_official_data_user_verification_status",
            ["user_pk", "verification_status"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("official_data_documents", schema=None) as batch_op:
        batch_op.drop_index("idx_official_data_user_verification_status")
        batch_op.drop_index("idx_official_data_user_trust_grade")
        batch_op.drop_constraint("ck_official_data_structure_validation_status", type_="check")
        batch_op.drop_constraint("ck_official_data_verification_status", type_="check")
        batch_op.drop_constraint("ck_official_data_trust_grade", type_="check")
        batch_op.drop_column("sensitive_data_redacted")
        batch_op.drop_column("user_modified_flag")
        batch_op.drop_column("verification_reference_masked")
        batch_op.drop_column("verification_checked_at")
        batch_op.drop_column("verification_status")
        batch_op.drop_column("verification_source")
        batch_op.drop_column("structure_validation_status")
        batch_op.drop_column("trust_scope_label")
        batch_op.drop_column("trust_grade_label")
        batch_op.drop_column("trust_grade")
