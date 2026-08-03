"""create video ingestion tables

Revision ID: 20260801_0001
Revises:
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260801_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("metadata_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )


def downgrade() -> None:
    op.drop_table("transcripts")
    op.drop_index("ix_videos_status", table_name="videos")
    op.drop_table("videos")
