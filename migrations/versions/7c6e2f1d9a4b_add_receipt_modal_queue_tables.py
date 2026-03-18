"""add receipt modal queue tables

Revision ID: 7c6e2f1d9a4b
Revises: 91cf2b0b8f3a
Create Date: 2026-03-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '7c6e2f1d9a4b'
down_revision = '91cf2b0b8f3a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'receipt_modal_jobs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_pk', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='queued'),
        sa.Column('storage_dir', sa.String(length=1024), nullable=False),
        sa.Column('parse_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('worker_id', sa.String(length=64), nullable=True),
        sa.Column('worker_claimed_at', sa.DateTime(), nullable=True),
        sa.Column('worker_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('last_result_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_pk'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed','created','created_partial')",
            name='ck_receipt_modal_jobs_status',
        ),
    )
    op.create_index('idx_receipt_modal_jobs_user_created', 'receipt_modal_jobs', ['user_pk', 'created_at'], unique=False)
    op.create_index('idx_receipt_modal_jobs_status_created', 'receipt_modal_jobs', ['status', 'created_at'], unique=False)
    op.create_index('idx_receipt_modal_jobs_worker_heartbeat', 'receipt_modal_jobs', ['status', 'worker_heartbeat_at'], unique=False)

    op.create_table(
        'receipt_modal_job_items',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('job_id', sa.String(length=32), nullable=False),
        sa.Column('user_pk', sa.Integer(), nullable=False),
        sa.Column('client_index', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=120), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stored_path', sa.String(length=1024), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='queued'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('occurred_on', sa.String(length=10), nullable=True),
        sa.Column('occurred_time', sa.String(length=5), nullable=True),
        sa.Column('amount_krw', sa.Integer(), nullable=True),
        sa.Column('counterparty', sa.String(length=80), nullable=True),
        sa.Column('payment_item', sa.String(length=120), nullable=True),
        sa.Column('payment_method', sa.String(length=80), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('usage', sa.String(length=16), nullable=False, server_default='unknown'),
        sa.Column('warnings_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_transaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_transaction_id'], ['transactions.id']),
        sa.ForeignKeyConstraint(['job_id'], ['receipt_modal_jobs.id']),
        sa.ForeignKeyConstraint(['user_pk'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'client_index', name='uq_receipt_modal_job_items_job_client'),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','error','created')",
            name='ck_receipt_modal_job_items_status',
        ),
        sa.CheckConstraint(
            "usage IN ('business','personal','unknown')",
            name='ck_receipt_modal_job_items_usage',
        ),
    )
    op.create_index('idx_receipt_modal_job_items_job_client', 'receipt_modal_job_items', ['job_id', 'client_index'], unique=False)
    op.create_index('idx_receipt_modal_job_items_user_created', 'receipt_modal_job_items', ['user_pk', 'created_at'], unique=False)
    op.create_index('idx_receipt_modal_job_items_status', 'receipt_modal_job_items', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_receipt_modal_job_items_status', table_name='receipt_modal_job_items')
    op.drop_index('idx_receipt_modal_job_items_user_created', table_name='receipt_modal_job_items')
    op.drop_index('idx_receipt_modal_job_items_job_client', table_name='receipt_modal_job_items')
    op.drop_table('receipt_modal_job_items')

    op.drop_index('idx_receipt_modal_jobs_worker_heartbeat', table_name='receipt_modal_jobs')
    op.drop_index('idx_receipt_modal_jobs_status_created', table_name='receipt_modal_jobs')
    op.drop_index('idx_receipt_modal_jobs_user_created', table_name='receipt_modal_jobs')
    op.drop_table('receipt_modal_jobs')
