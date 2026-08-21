from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.publish_job import PublishPlatform

if TYPE_CHECKING:
    from app.models.publish_job import PublishJob


class PublishingUploadSession(Base):
    __tablename__ = "publishing_upload_sessions"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publishing_upload_sessions_platform",
        ),
        CheckConstraint(
            "length(trim(encrypted_session_uri)) > 0",
            name="ck_publishing_upload_sessions_session_uri_non_empty",
        ),
        CheckConstraint(
            "total_bytes > 0",
            name="ck_publishing_upload_sessions_total_bytes_positive",
        ),
        CheckConstraint(
            "next_byte_offset >= 0 AND next_byte_offset < total_bytes",
            name="ck_publishing_upload_sessions_offset_in_range",
        ),
        CheckConstraint(
            "(execution_owner IS NULL AND execution_lease_expires_at IS NULL) OR "
            "(execution_owner IS NOT NULL AND execution_lease_expires_at IS NOT NULL)",
            name="ck_publishing_upload_sessions_execution_lease_paired",
        ),
        UniqueConstraint(
            "publish_job_id",
            name="uq_publishing_upload_sessions_publish_job_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publish_job_id: Mapped[int] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), nullable=False
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
    )
    encrypted_session_uri: Mapped[str] = mapped_column(Text, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    next_byte_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    execution_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    publish_job: Mapped[PublishJob] = relationship(back_populates="upload_session")
