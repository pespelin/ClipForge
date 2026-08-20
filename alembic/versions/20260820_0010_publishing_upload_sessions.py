"""create publishing upload sessions table

Revision ID: 20260820_0010
Revises: 20260818_0009
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from alembic import op

revision = "20260820_0010"
down_revision = "20260818_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publishing_upload_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publish_job_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("encrypted_session_uri", sa.Text(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("next_byte_offset", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publishing_upload_sessions_platform",
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_session_uri)) > 0",
            name="ck_publishing_upload_sessions_session_uri_non_empty",
        ),
        sa.CheckConstraint(
            "total_bytes > 0",
            name="ck_publishing_upload_sessions_total_bytes_positive",
        ),
        sa.CheckConstraint(
            "next_byte_offset >= 0 AND next_byte_offset < total_bytes",
            name="ck_publishing_upload_sessions_offset_in_range",
        ),
        sa.ForeignKeyConstraint(["publish_job_id"], ["publish_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publish_job_id",
            name="uq_publishing_upload_sessions_publish_job_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("publishing_upload_sessions")
