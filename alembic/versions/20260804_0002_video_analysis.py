"""create video analysis table

Revision ID: 20260804_0002
Revises: 20260801_0001
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260804_0002"
down_revision = "20260801_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("sentiment", sa.String(length=64), nullable=True),
        sa.Column("hook_candidates", sa.JSON(), nullable=False),
        sa.Column("clip_candidates", sa.JSON(), nullable=False),
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
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_video_analyses_status",
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index("ix_video_analyses_status", "video_analyses", ["status"])


def downgrade() -> None:
    op.drop_index("ix_video_analyses_status", table_name="video_analyses")
    op.drop_table("video_analyses")
