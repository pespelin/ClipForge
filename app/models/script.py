from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
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
    from app.models.video import Video
    from app.models.video_analysis import VideoAnalysis
    from app.models.video_render import VideoRender
    from app.models.voice_track import VoiceTrack


class ScriptStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ScriptTone(StrEnum):
    ENGAGING = "engaging"
    EDUCATIONAL = "educational"
    DRAMATIC = "dramatic"
    HUMOROUS = "humorous"
    INSPIRATIONAL = "inspirational"
    NEUTRAL = "neutral"


class Script(Base):
    __tablename__ = "scripts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'generating', 'completed', 'failed')",
            name="ck_scripts_status",
        ),
        CheckConstraint(
            "tone IN ('engaging', 'educational', 'dramatic', 'humorous', "
            "'inspirational', 'neutral')",
            name="ck_scripts_tone",
        ),
        CheckConstraint(
            "target_duration_seconds > 0",
            name="ck_scripts_target_duration_positive",
        ),
        CheckConstraint(
            "estimated_duration_seconds IS NULL OR estimated_duration_seconds >= 0",
            name="ck_scripts_estimated_duration_non_negative",
        ),
        CheckConstraint(
            "status != 'completed' OR (title IS NOT NULL AND hook IS NOT NULL "
            "AND body IS NOT NULL AND full_script IS NOT NULL "
            "AND length(trim(title)) > 0 AND length(trim(hook)) > 0 "
            "AND length(trim(body)) > 0 AND length(trim(full_script)) > 0)",
            name="ck_scripts_completed_content",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("video_analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ScriptStatus] = mapped_column(
        Enum(
            ScriptStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=ScriptStatus.PENDING,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_to_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    tone: Mapped[ScriptTone] = mapped_column(
        Enum(
            ScriptTone,
            values_callable=lambda tones: [tone.value for tone in tones],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=ScriptTone.ENGAGING,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    generation_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    video: Mapped[Video] = relationship(back_populates="scripts")
    video_analysis: Mapped[VideoAnalysis] = relationship(back_populates="scripts")
    voice_tracks: Mapped[list[VoiceTrack]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )
    broll_collections: Mapped[list[BrollCollection]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )
    video_renders: Mapped[list[VideoRender]] = relationship(
        back_populates="script", cascade="all, delete-orphan", passive_deletes=True
    )
