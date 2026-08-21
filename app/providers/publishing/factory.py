from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.providers.oauth import GoogleOAuthTokenRefreshProvider
from app.providers.publishing.base import PublishingProvider
from app.providers.publishing.credential_resolver import OAuthPublishingCredentialResolver
from app.providers.publishing.filesystem import FilesystemPublishingArtifactReader
from app.providers.publishing.local import LocalPublishingProvider
from app.providers.publishing.youtube import YouTubePublishingProvider
from app.repositories.oauth_credential_repository import OAuthCredentialRepository
from app.repositories.publishing_account_repository import PublishingAccountRepository
from app.repositories.publishing_upload_session_repository import (
    PublishingUploadSessionRepository,
)
from app.security import CredentialEncryptionError, FernetCredentialEncryptor
from app.services.oauth_credential_resolver import OAuthCredentialResolver
from app.services.oauth_credential_service import OAuthCredentialService
from app.services.publishing_upload_session_service import PublishingUploadSessionService


class PublishingProviderConfigurationError(AppError):
    """Raised when the selected publishing provider cannot be composed safely."""

    status_code = 503
    detail = "Publishing provider is not configured"


@dataclass(frozen=True, slots=True)
class PublishingComposition:
    provider: PublishingProvider
    upload_session_service: PublishingUploadSessionService | None = None


def create_publishing_composition(
    *,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PublishingComposition:
    settings = settings or get_settings()
    if settings.publishing_provider == "local":
        return PublishingComposition(provider=LocalPublishingProvider())

    client_secret = (
        settings.youtube_oauth_client_secret.get_secret_value()
        if settings.youtube_oauth_client_secret is not None
        else None
    )
    encryption_key = (
        settings.credential_encryption_key.get_secret_value()
        if settings.credential_encryption_key is not None
        else None
    )
    if not (
        session is not None
        and http_client is not None
        and settings.youtube_oauth_client_id
        and client_secret
        and encryption_key
    ):
        raise PublishingProviderConfigurationError
    try:
        encryptor = FernetCredentialEncryptor(encryption_key)
    except CredentialEncryptionError:
        raise PublishingProviderConfigurationError from None

    credential_service = OAuthCredentialService(
        OAuthCredentialRepository(session),
        encryptor,
    )
    oauth_resolver = OAuthCredentialResolver(
        credential_service,
        GoogleOAuthTokenRefreshProvider(
            http_client,
            client_id=settings.youtube_oauth_client_id,
            client_secret=client_secret,
        ),
    )
    provider = YouTubePublishingProvider(
        OAuthPublishingCredentialResolver(
            PublishingAccountRepository(session),
            oauth_resolver,
        ),
        FilesystemPublishingArtifactReader(settings.storage_root),
        http_client,
    )
    return PublishingComposition(
        provider=provider,
        upload_session_service=PublishingUploadSessionService(
            PublishingUploadSessionRepository(session),
            encryptor,
        ),
    )


def create_publishing_provider(
    *,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PublishingProvider:
    return create_publishing_composition(
        settings=settings,
        session=session,
        http_client=http_client,
    ).provider
