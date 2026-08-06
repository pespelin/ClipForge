"""create video rendering table

Revision ID: 20260807_0006
Revises: 20260805_0005
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "20260807_0006"
down_revision = "20260805_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_renders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("voice_track_id", sa.Integer(), nullable=False),
        sa.Column("broll_collection_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output_format", sa.String(length=16), nullable=False),
        sa.Column("video_codec", sa.String(length=16), nullable=False),
        sa.Column("audio_codec", sa.String(length=16), nullable=False),
        sa.Column("resolution_preset", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps", sa.Float(), nullable=False),
        sa.Column("fit_mode", sa.String(length=16), nullable=False),
        sa.Column("background_color", sa.String(length=7), nullable=False),
        sa.Column("subtitle_enabled", sa.Boolean(), nullable=False),
        sa.Column("subtitle_style", sa.JSON(), nullable=False),
        sa.Column("render_options", sa.JSON(), nullable=False),
        sa.Column("timeline_data", sa.JSON(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(length=256), nullable=True),
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
            "status IN ('pending', 'rendering', 'completed', 'failed')",
            name="ck_video_renders_status",
        ),
        sa.CheckConstraint(
            "output_format IN ('mp4', 'mov', 'webm')",
            name="ck_video_renders_output_format",
        ),
        sa.CheckConstraint(
            "video_codec IN ('h264', 'hevc', 'vp9')",
            name="ck_video_renders_video_codec",
        ),
        sa.CheckConstraint(
            "audio_codec IN ('aac', 'opus', 'pcm')",
            name="ck_video_renders_audio_codec",
        ),
        sa.CheckConstraint(
            "resolution_preset IN ('vertical_1080x1920', 'vertical_720x1280', "
            "'square_1080x1080', 'landscape_1920x1080', 'custom')",
            name="ck_video_renders_resolution_preset",
        ),
        sa.CheckConstraint(
            "fit_mode IN ('cover', 'contain', 'stretch')",
            name="ck_video_renders_fit_mode",
        ),
        sa.CheckConstraint("width > 0", name="ck_video_renders_width_positive"),
        sa.CheckConstraint("height > 0", name="ck_video_renders_height_positive"),
        sa.CheckConstraint("fps BETWEEN 1.0 AND 120.0", name="ck_video_renders_fps_range"),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_video_renders_duration_non_negative",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_video_renders_file_size_non_negative",
        ),
        sa.CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_video_renders_checksum_non_empty",
        ),
        sa.CheckConstraint(
            "background_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_video_renders_background_color_hex",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (storage_key IS NOT NULL "
            "AND length(trim(storage_key)) > 0 AND duration_seconds > 0 "
            "AND file_size_bytes IS NOT NULL AND file_size_bytes >= 0)",
            name="ck_video_renders_completed_content",
        ),
        sa.CheckConstraint(
            "(resolution_preset = 'custom') OR "
            "(resolution_preset = 'vertical_1080x1920' AND width = 1080 AND height = 1920) OR "
            "(resolution_preset = 'vertical_720x1280' AND width = 720 AND height = 1280) OR "
            "(resolution_preset = 'square_1080x1080' AND width = 1080 AND height = 1080) OR "
            "(resolution_preset = 'landscape_1920x1080' AND width = 1920 AND height = 1080)",
            name="ck_video_renders_preset_dimensions",
        ),
        sa.ForeignKeyConstraint(["script_id"], ["scripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voice_track_id"], ["voice_tracks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["broll_collection_id"], ["broll_collections.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_video_renders_script_id", "video_renders", ["script_id"])
    op.create_index("ix_video_renders_voice_track_id", "video_renders", ["voice_track_id"])
    op.create_index(
        "ix_video_renders_broll_collection_id", "video_renders", ["broll_collection_id"]
    )
    op.create_index("ix_video_renders_status", "video_renders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_video_renders_status", table_name="video_renders")
    op.drop_index("ix_video_renders_broll_collection_id", table_name="video_renders")
    op.drop_index("ix_video_renders_voice_track_id", table_name="video_renders")
    op.drop_index("ix_video_renders_script_id", table_name="video_renders")
    op.drop_table("video_renders")
