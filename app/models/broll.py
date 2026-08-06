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
    from app.models.video_render import VideoRender


class BrollCollectionStatus(StrEnum):
    PENDING = "pending"
    SEARCHING = "searching"
    COMPLETED = "completed"
    FAILED = "failed"


class BrollAssetStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    DOWNLOADED = "downloaded"
    REJECTED = "rejected"
    FAILED = "failed"


class BrollMediaType(StrEnum):
    VIDEO = "video"
    IMAGE = "image"


class BrollOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"
    UNKNOWN = "unknown"


class BrollProvider(StrEnum):
    LOCAL = "local"
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    UNSPLASH = "unsplash"
    OTHER = "other"


class BrollCollection(Base):
    __tablename__ = "broll_collections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'searching', 'completed', 'failed')",
            name="ck_broll_collections_status",
        ),
        CheckConstraint(
            "provider IN ('local', 'pexels', 'pixabay', 'unsplash', 'other')",
            name="ck_broll_collections_provider",
        ),
        CheckConstraint(
            "length(trim(query_strategy)) > 0",
            name="ck_broll_collections_query_strategy_non_empty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[BrollCollectionStatus] = mapped_column(
        Enum(
            BrollCollectionStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=BrollCollectionStatus.PENDING,
        index=True,
    )
    provider: Mapped[BrollProvider] = mapped_column(
        Enum(
            BrollProvider,
            values_callable=lambda providers: [provider.value for provider in providers],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=BrollProvider.LOCAL,
    )
    query_strategy: Mapped[str] = mapped_column(
        String(128), nullable=False, default="section_keywords"
    )
    retrieval_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    script: Mapped[Script] = relationship(back_populates="broll_collections")
    assets: Mapped[list[BrollAsset]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )
    video_renders: Mapped[list[VideoRender]] = relationship(
        back_populates="broll_collection", passive_deletes=True
    )


class BrollAsset(Base):
    """Persisted B-roll candidate or artifact.

    Future orchestration should populate downloaded_at only when status is downloaded.
    """

    __tablename__ = "broll_assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'selected', 'downloaded', 'rejected', 'failed')",
            name="ck_broll_assets_status",
        ),
        CheckConstraint(
            "provider IN ('local', 'pexels', 'pixabay', 'unsplash', 'other')",
            name="ck_broll_assets_provider",
        ),
        CheckConstraint(
            "media_type IN ('video', 'image')",
            name="ck_broll_assets_media_type",
        ),
        CheckConstraint(
            "orientation IN ('portrait', 'landscape', 'square', 'unknown')",
            name="ck_broll_assets_orientation",
        ),
        CheckConstraint(
            "script_section_order IS NULL OR script_section_order >= 0",
            name="ck_broll_assets_section_order_non_negative",
        ),
        CheckConstraint(
            "length(trim(query)) > 0",
            name="ck_broll_assets_query_non_empty",
        ),
        CheckConstraint(
            "external_id IS NULL OR length(trim(external_id)) > 0",
            name="ck_broll_assets_external_id_non_empty",
        ),
        CheckConstraint(
            "source_url IS NULL OR length(trim(source_url)) > 0",
            name="ck_broll_assets_source_url_non_empty",
        ),
        CheckConstraint(
            "preview_url IS NULL OR length(trim(preview_url)) > 0",
            name="ck_broll_assets_preview_url_non_empty",
        ),
        CheckConstraint(
            "download_url IS NULL OR length(trim(download_url)) > 0",
            name="ck_broll_assets_download_url_non_empty",
        ),
        CheckConstraint(
            "width IS NULL OR width > 0",
            name="ck_broll_assets_width_positive",
        ),
        CheckConstraint(
            "height IS NULL OR height > 0",
            name="ck_broll_assets_height_positive",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_broll_assets_duration_non_negative",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_broll_assets_file_size_non_negative",
        ),
        CheckConstraint(
            "relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0",
            name="ck_broll_assets_relevance_range",
        ),
        CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_broll_assets_checksum_non_empty",
        ),
        CheckConstraint(
            "status != 'downloaded' OR (storage_key IS NOT NULL AND length(trim(storage_key)) > 0)",
            name="ck_broll_assets_downloaded_storage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("broll_collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_section_order: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider: Mapped[BrollProvider] = mapped_column(
        Enum(
            BrollProvider,
            values_callable=lambda providers: [provider.value for provider in providers],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=BrollProvider.LOCAL,
    )
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    media_type: Mapped[BrollMediaType] = mapped_column(
        Enum(
            BrollMediaType,
            values_callable=lambda media_types: [media_type.value for media_type in media_types],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=BrollMediaType.VIDEO,
    )
    status: Mapped[BrollAssetStatus] = mapped_column(
        Enum(
            BrollAssetStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=BrollAssetStatus.CANDIDATE,
        index=True,
    )
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    photographer_or_creator: Mapped[str | None] = mapped_column(String(512), nullable=True)
    orientation: Mapped[BrollOrientation] = mapped_column(
        Enum(
            BrollOrientation,
            values_callable=lambda orientations: [
                orientation.value for orientation in orientations
            ],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=BrollOrientation.UNKNOWN,
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    collection: Mapped[BrollCollection] = relationship(back_populates="assets")
