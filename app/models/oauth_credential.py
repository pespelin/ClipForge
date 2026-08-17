from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.publishing_account import PublishingAccount


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"
    __table_args__ = (
        CheckConstraint(
            "length(trim(encrypted_access_token)) > 0",
            name="ck_oauth_credentials_access_token_non_empty",
        ),
        CheckConstraint(
            "encrypted_refresh_token IS NULL OR length(trim(encrypted_refresh_token)) > 0",
            name="ck_oauth_credentials_refresh_token_non_empty",
        ),
        CheckConstraint(
            "token_type IS NULL OR length(trim(token_type)) > 0",
            name="ck_oauth_credentials_token_type_non_empty",
        ),
        CheckConstraint(
            "scope IS NULL OR length(trim(scope)) > 0",
            name="ck_oauth_credentials_scope_non_empty",
        ),
        UniqueConstraint(
            "publishing_account_id",
            name="uq_oauth_credentials_publishing_account_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publishing_account_id: Mapped[int] = mapped_column(
        ForeignKey("publishing_accounts.id", ondelete="CASCADE"), nullable=False
    )
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    publishing_account: Mapped[PublishingAccount] = relationship(back_populates="oauth_credential")
