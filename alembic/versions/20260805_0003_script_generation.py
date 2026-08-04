"""create script generation table

Revision ID: 20260805_0003
Revises: 20260804_0002
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0003"
down_revision = "20260804_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("video_analysis_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("call_to_action", sa.Text(), nullable=True),
        sa.Column("full_script", sa.Text(), nullable=True),
        sa.Column("estimated_duration_seconds", sa.Float(), nullable=True),
        sa.Column("target_duration_seconds", sa.Float(), nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("generation_options", sa.JSON(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
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
            "status IN ('pending', 'generating', 'completed', 'failed')",
            name="ck_scripts_status",
        ),
        sa.CheckConstraint(
            "tone IN ('engaging', 'educational', 'dramatic', 'humorous', "
            "'inspirational', 'neutral')",
            name="ck_scripts_tone",
        ),
        sa.CheckConstraint(
            "target_duration_seconds > 0",
            name="ck_scripts_target_duration_positive",
        ),
        sa.CheckConstraint(
            "estimated_duration_seconds IS NULL OR estimated_duration_seconds >= 0",
            name="ck_scripts_estimated_duration_non_negative",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (title IS NOT NULL AND hook IS NOT NULL "
            "AND body IS NOT NULL AND full_script IS NOT NULL "
            "AND length(trim(title)) > 0 AND length(trim(hook)) > 0 "
            "AND length(trim(body)) > 0 AND length(trim(full_script)) > 0)",
            name="ck_scripts_completed_content",
        ),
        sa.ForeignKeyConstraint(["video_analysis_id"], ["video_analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scripts_status", "scripts", ["status"])
    op.create_index("ix_scripts_video_analysis_id", "scripts", ["video_analysis_id"])
    op.create_index("ix_scripts_video_id", "scripts", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_scripts_video_id", table_name="scripts")
    op.drop_index("ix_scripts_video_analysis_id", table_name="scripts")
    op.drop_index("ix_scripts_status", table_name="scripts")
    op.drop_table("scripts")
