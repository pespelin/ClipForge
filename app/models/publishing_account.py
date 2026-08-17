from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.publish_job import PublishPlatform

if TYPE_CHECKING:
    from app.models.oauth_credential import OAuthCredential


class PublishingAccount(Base):
    __tablename__ = "publishing_accounts"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publishing_accounts_platform",
        ),
        CheckConstraint(
            "length(trim(remote_account_id)) > 0",
            name="ck_publishing_accounts_remote_account_id_non_empty",
        ),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_publishing_accounts_display_name_non_empty",
        ),
        UniqueConstraint(
            "platform",
            "remote_account_id",
            name="uq_publishing_accounts_platform_remote_account_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[PublishPlatform] = mapped_column(
        Enum(
            PublishPlatform,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        index=True,
    )
    remote_account_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    oauth_credential: Mapped[OAuthCredential | None] = relationship(
        back_populates="publishing_account",
        cascade="all, delete-orphan",
        single_parent=True,
        passive_deletes=True,
    )
