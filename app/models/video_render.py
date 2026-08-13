from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.broll import BrollCollection
    from app.models.publish_job import PublishJob
    from app.models.script import Script
    from app.models.voice_track import VoiceTrack


class VideoRenderStatus(StrEnum):
    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


class RenderOutputFormat(StrEnum):
    MP4 = "mp4"
    MOV = "mov"
    WEBM = "webm"


class VideoCodec(StrEnum):
    H264 = "h264"
    HEVC = "hevc"
    VP9 = "vp9"


class RenderAudioCodec(StrEnum):
    AAC = "aac"
    OPUS = "opus"
    PCM = "pcm"


class ResolutionPreset(StrEnum):
    VERTICAL_1080X1920 = "vertical_1080x1920"
    VERTICAL_720X1280 = "vertical_720x1280"
    SQUARE_1080X1080 = "square_1080x1080"
    LANDSCAPE_1920X1080 = "landscape_1920x1080"
    CUSTOM = "custom"


class RenderFitMode(StrEnum):
    COVER = "cover"
    CONTAIN = "contain"
    STRETCH = "stretch"


class RenderTimelineItemType(StrEnum):
    BROLL_VIDEO = "broll_video"
    BROLL_IMAGE = "broll_image"
    NARRATION = "narration"
    SUBTITLE = "subtitle"
    BACKGROUND = "background"
    GAP = "gap"


class VideoRender(Base):
    __tablename__ = "video_renders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'rendering', 'completed', 'failed')",
            name="ck_video_renders_status",
        ),
        CheckConstraint(
            "output_format IN ('mp4', 'mov', 'webm')",
            name="ck_video_renders_output_format",
        ),
        CheckConstraint(
            "video_codec IN ('h264', 'hevc', 'vp9')",
            name="ck_video_renders_video_codec",
        ),
        CheckConstraint(
            "audio_codec IN ('aac', 'opus', 'pcm')",
            name="ck_video_renders_audio_codec",
        ),
        CheckConstraint(
            "resolution_preset IN ('vertical_1080x1920', 'vertical_720x1280', "
            "'square_1080x1080', 'landscape_1920x1080', 'custom')",
            name="ck_video_renders_resolution_preset",
        ),
        CheckConstraint(
            "fit_mode IN ('cover', 'contain', 'stretch')",
            name="ck_video_renders_fit_mode",
        ),
        CheckConstraint("width > 0", name="ck_video_renders_width_positive"),
        CheckConstraint("height > 0", name="ck_video_renders_height_positive"),
        CheckConstraint("fps BETWEEN 1.0 AND 120.0", name="ck_video_renders_fps_range"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_video_renders_duration_non_negative",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_video_renders_file_size_non_negative",
        ),
        CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_video_renders_checksum_non_empty",
        ),
        CheckConstraint(
            "background_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_video_renders_background_color_hex",
        ),
        CheckConstraint(
            "status != 'completed' OR (storage_key IS NOT NULL "
            "AND length(trim(storage_key)) > 0 AND duration_seconds > 0 "
            "AND file_size_bytes IS NOT NULL AND file_size_bytes >= 0)",
            name="ck_video_renders_completed_content",
        ),
        CheckConstraint(
            "(resolution_preset = 'custom') OR "
            "(resolution_preset = 'vertical_1080x1920' AND width = 1080 AND height = 1920) OR "
            "(resolution_preset = 'vertical_720x1280' AND width = 720 AND height = 1280) OR "
            "(resolution_preset = 'square_1080x1080' AND width = 1080 AND height = 1080) OR "
            "(resolution_preset = 'landscape_1920x1080' AND width = 1920 AND height = 1080)",
            name="ck_video_renders_preset_dimensions",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voice_track_id: Mapped[int] = mapped_column(
        ForeignKey("voice_tracks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    broll_collection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broll_collections.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[VideoRenderStatus] = mapped_column(
        Enum(
            VideoRenderStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=VideoRenderStatus.PENDING,
        index=True,
    )
    output_format: Mapped[RenderOutputFormat] = mapped_column(
        Enum(
            RenderOutputFormat,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=RenderOutputFormat.MP4,
    )
    video_codec: Mapped[VideoCodec] = mapped_column(
        Enum(
            VideoCodec,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=VideoCodec.H264,
    )
    audio_codec: Mapped[RenderAudioCodec] = mapped_column(
        Enum(
            RenderAudioCodec,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=RenderAudioCodec.AAC,
    )
    resolution_preset: Mapped[ResolutionPreset] = mapped_column(
        Enum(
            ResolutionPreset,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=ResolutionPreset.VERTICAL_1080X1920,
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    fps: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    fit_mode: Mapped[RenderFitMode] = mapped_column(
        Enum(
            RenderFitMode,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=RenderFitMode.COVER,
    )
    background_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#000000")
    subtitle_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    subtitle_style: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    render_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timeline_data: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    script: Mapped[Script] = relationship(back_populates="video_renders")
    voice_track: Mapped[VoiceTrack] = relationship(back_populates="video_renders")
    broll_collection: Mapped[BrollCollection | None] = relationship(back_populates="video_renders")
    publish_jobs: Mapped[list[PublishJob]] = relationship(
        back_populates="video_render", passive_deletes="all"
    )
