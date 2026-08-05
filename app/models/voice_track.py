from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
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
    from app.models.script import Script


class VoiceTrackStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class VoiceStyle(StrEnum):
    NEUTRAL = "neutral"
    CONVERSATIONAL = "conversational"
    ENERGETIC = "energetic"
    CALM = "calm"
    DRAMATIC = "dramatic"
    INSPIRATIONAL = "inspirational"


class AudioFormat(StrEnum):
    WAV = "wav"
    MP3 = "mp3"


class VoiceTrack(Base):
    __tablename__ = "voice_tracks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'generating', 'completed', 'failed')",
            name="ck_voice_tracks_status",
        ),
        CheckConstraint(
            "style IN ('neutral', 'conversational', 'energetic', 'calm', "
            "'dramatic', 'inspirational')",
            name="ck_voice_tracks_style",
        ),
        CheckConstraint(
            "audio_format IN ('wav', 'mp3')",
            name="ck_voice_tracks_audio_format",
        ),
        CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_voice_tracks_provider_non_empty",
        ),
        CheckConstraint(
            "length(trim(voice)) > 0",
            name="ck_voice_tracks_voice_non_empty",
        ),
        CheckConstraint(
            "sample_rate_hz BETWEEN 8000 AND 192000",
            name="ck_voice_tracks_sample_rate_range",
        ),
        CheckConstraint(
            "speaking_rate BETWEEN 0.5 AND 2.0",
            name="ck_voice_tracks_speaking_rate_range",
        ),
        CheckConstraint(
            "pitch BETWEEN -20.0 AND 20.0",
            name="ck_voice_tracks_pitch_range",
        ),
        CheckConstraint(
            "volume_gain_db BETWEEN -60.0 AND 20.0",
            name="ck_voice_tracks_volume_gain_range",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_voice_tracks_duration_non_negative",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_voice_tracks_file_size_non_negative",
        ),
        CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_voice_tracks_checksum_non_empty",
        ),
        CheckConstraint(
            "status != 'completed' OR (storage_key IS NOT NULL "
            "AND length(trim(storage_key)) > 0 AND duration_seconds > 0 "
            "AND file_size_bytes IS NOT NULL AND file_size_bytes >= 0)",
            name="ck_voice_tracks_completed_content",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[VoiceTrackStatus] = mapped_column(
        Enum(
            VoiceTrackStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=VoiceTrackStatus.PENDING,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    voice: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    style: Mapped[VoiceStyle] = mapped_column(
        Enum(
            VoiceStyle,
            values_callable=lambda styles: [style.value for style in styles],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=VoiceStyle.NEUTRAL,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    audio_format: Mapped[AudioFormat] = mapped_column(
        Enum(
            AudioFormat,
            values_callable=lambda formats: [audio_format.value for audio_format in formats],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=AudioFormat.WAV,
    )
    sample_rate_hz: Mapped[int] = mapped_column(Integer, nullable=False, default=24000)
    speaking_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    pitch: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volume_gain_db: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    generation_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
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

    script: Mapped[Script] = relationship(back_populates="voice_tracks")
