"""create voice track table

Revision ID: 20260805_0004
Revises: 20260805_0003
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0004"
down_revision = "20260805_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_tracks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("voice", sa.String(length=128), nullable=False),
        sa.Column("style", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("audio_format", sa.String(length=16), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("speaking_rate", sa.Float(), nullable=False),
        sa.Column("pitch", sa.Float(), nullable=False),
        sa.Column("volume_gain_db", sa.Float(), nullable=False),
        sa.Column("generation_options", sa.JSON(), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
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
            "status IN ('pending', 'generating', 'completed', 'failed')",
            name="ck_voice_tracks_status",
        ),
        sa.CheckConstraint(
            "style IN ('neutral', 'conversational', 'energetic', 'calm', "
            "'dramatic', 'inspirational')",
            name="ck_voice_tracks_style",
        ),
        sa.CheckConstraint(
            "audio_format IN ('wav', 'mp3')",
            name="ck_voice_tracks_audio_format",
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_voice_tracks_provider_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(voice)) > 0",
            name="ck_voice_tracks_voice_non_empty",
        ),
        sa.CheckConstraint(
            "sample_rate_hz BETWEEN 8000 AND 192000",
            name="ck_voice_tracks_sample_rate_range",
        ),
        sa.CheckConstraint(
            "speaking_rate BETWEEN 0.5 AND 2.0",
            name="ck_voice_tracks_speaking_rate_range",
        ),
        sa.CheckConstraint(
            "pitch BETWEEN -20.0 AND 20.0",
            name="ck_voice_tracks_pitch_range",
        ),
        sa.CheckConstraint(
            "volume_gain_db BETWEEN -60.0 AND 20.0",
            name="ck_voice_tracks_volume_gain_range",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_voice_tracks_duration_non_negative",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_voice_tracks_file_size_non_negative",
        ),
        sa.CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_voice_tracks_checksum_non_empty",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (storage_key IS NOT NULL "
            "AND length(trim(storage_key)) > 0 AND duration_seconds > 0 "
            "AND file_size_bytes IS NOT NULL AND file_size_bytes >= 0)",
            name="ck_voice_tracks_completed_content",
        ),
        sa.ForeignKeyConstraint(["script_id"], ["scripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_tracks_script_id", "voice_tracks", ["script_id"])
    op.create_index("ix_voice_tracks_status", "voice_tracks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_voice_tracks_status", table_name="voice_tracks")
    op.drop_index("ix_voice_tracks_script_id", table_name="voice_tracks")
    op.drop_table("voice_tracks")
