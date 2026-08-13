from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    from app.models.video_render import VideoRender


class PublishStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishPlatform(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    OTHER = "other"


class PublishVisibility(StrEnum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'failed', 'cancelled')",
            name="ck_publish_jobs_status",
        ),
        CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publish_jobs_platform",
        ),
        CheckConstraint(
            "visibility IN ('public', 'unlisted', 'private')",
            name="ck_publish_jobs_visibility",
        ),
        CheckConstraint(
            "length(trim(account_reference)) > 0",
            name="ck_publish_jobs_account_reference_non_empty",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_publish_jobs_title_non_empty"),
        CheckConstraint(
            "category IS NULL OR length(trim(category)) > 0",
            name="ck_publish_jobs_category_non_empty",
        ),
        CheckConstraint(
            "length(trim(source_storage_key)) > 0",
            name="ck_publish_jobs_source_storage_key_non_empty",
        ),
        CheckConstraint(
            "source_checksum IS NULL OR length(trim(source_checksum)) > 0",
            name="ck_publish_jobs_source_checksum_non_empty",
        ),
        CheckConstraint(
            "source_file_size_bytes >= 0",
            name="ck_publish_jobs_source_file_size_non_negative",
        ),
        CheckConstraint(
            "source_duration_seconds > 0",
            name="ck_publish_jobs_source_duration_positive",
        ),
        CheckConstraint(
            "remote_media_id IS NULL OR length(trim(remote_media_id)) > 0",
            name="ck_publish_jobs_remote_media_id_non_empty",
        ),
        CheckConstraint(
            "remote_url IS NULL OR length(trim(remote_url)) > 0",
            name="ck_publish_jobs_remote_url_non_empty",
        ),
        CheckConstraint(
            "status != 'published' OR (remote_media_id IS NOT NULL "
            "AND length(trim(remote_media_id)) > 0 AND published_at IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_publish_jobs_published_content",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_render_id: Mapped[int] = mapped_column(
        ForeignKey("video_renders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[PublishStatus] = mapped_column(
        Enum(
            PublishStatus,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=PublishStatus.PENDING,
        index=True,
    )
    platform: Mapped[PublishPlatform] = mapped_column(
        Enum(
            PublishPlatform,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=PublishPlatform.YOUTUBE,
        index=True,
    )
    account_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    visibility: Mapped[PublishVisibility] = mapped_column(
        Enum(
            PublishVisibility,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=PublishVisibility.PRIVATE,
    )
    made_for_kids: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_subscribers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    recording_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publish_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_checksum: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    remote_media_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    video_render: Mapped[VideoRender] = relationship(back_populates="publish_jobs")
