"""create publishing jobs table

Revision ID: 20260813_0007
Revises: 20260807_0006
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "20260813_0007"
down_revision = "20260807_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_render_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("account_reference", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("made_for_kids", sa.Boolean(), nullable=False),
        sa.Column("notify_subscribers", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("recording_date", sa.Date(), nullable=True),
        sa.Column("scheduled_publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_options", sa.JSON(), nullable=False),
        sa.Column("source_storage_key", sa.String(length=1024), nullable=False),
        sa.Column("source_checksum", sa.String(length=256), nullable=True),
        sa.Column("source_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_duration_seconds", sa.Float(), nullable=False),
        sa.Column("remote_media_id", sa.String(length=512), nullable=True),
        sa.Column("remote_url", sa.String(length=2048), nullable=True),
        sa.Column("remote_status", sa.String(length=200), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'failed', 'cancelled')",
            name="ck_publish_jobs_status",
        ),
        sa.CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publish_jobs_platform",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'unlisted', 'private')",
            name="ck_publish_jobs_visibility",
        ),
        sa.CheckConstraint(
            "length(trim(account_reference)) > 0",
            name="ck_publish_jobs_account_reference_non_empty",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_publish_jobs_title_non_empty"),
        sa.CheckConstraint(
            "category IS NULL OR length(trim(category)) > 0",
            name="ck_publish_jobs_category_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(source_storage_key)) > 0",
            name="ck_publish_jobs_source_storage_key_non_empty",
        ),
        sa.CheckConstraint(
            "source_checksum IS NULL OR length(trim(source_checksum)) > 0",
            name="ck_publish_jobs_source_checksum_non_empty",
        ),
        sa.CheckConstraint(
            "source_file_size_bytes >= 0",
            name="ck_publish_jobs_source_file_size_non_negative",
        ),
        sa.CheckConstraint(
            "source_duration_seconds > 0",
            name="ck_publish_jobs_source_duration_positive",
        ),
        sa.CheckConstraint(
            "remote_media_id IS NULL OR length(trim(remote_media_id)) > 0",
            name="ck_publish_jobs_remote_media_id_non_empty",
        ),
        sa.CheckConstraint(
            "remote_url IS NULL OR length(trim(remote_url)) > 0",
            name="ck_publish_jobs_remote_url_non_empty",
        ),
        sa.CheckConstraint(
            "status != 'published' OR (remote_media_id IS NOT NULL "
            "AND length(trim(remote_media_id)) > 0 AND published_at IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_publish_jobs_published_content",
        ),
        sa.ForeignKeyConstraint(["video_render_id"], ["video_renders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publish_jobs_video_render_id", "publish_jobs", ["video_render_id"])
    op.create_index("ix_publish_jobs_status", "publish_jobs", ["status"])
    op.create_index("ix_publish_jobs_platform", "publish_jobs", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_publish_jobs_platform", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_video_render_id", table_name="publish_jobs")
    op.drop_table("publish_jobs")
