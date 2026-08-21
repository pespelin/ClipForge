from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.exceptions import (
    OAuthCredentialPersistenceError,
    OAuthCredentialRefreshFailedError,
    OAuthCredentialRefreshUnavailableError,
    OAuthCredentialUnavailableError,
    PublishingAuthenticationError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.providers.oauth import (
    OAuthTokenRefreshAuthenticationError,
    OAuthTokenRefreshError,
    OAuthTokenRefreshProvider,
    OAuthTokenRefreshRateLimitError,
    OAuthTokenRefreshTransientError,
)
from app.security import CredentialEncryptionError
from app.services.oauth_credential_service import OAuthCredentialInput, OAuthCredentialService


@dataclass(frozen=True, slots=True)
class ResolvedOAuthCredential:
    access_token: str = field(repr=False)
    token_type: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None


class OAuthCredentialResolver:
    REFRESH_SKEW = timedelta(seconds=60)

    def __init__(
        self,
        credential_service: OAuthCredentialService,
        refresh_provider: OAuthTokenRefreshProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._credential_service = credential_service
        self._refresh_provider = refresh_provider
        self._clock = clock

    async def resolve(self, publishing_account_id: int) -> ResolvedOAuthCredential:
        try:
            credential = await self._credential_service.get_decrypted_by_publishing_account_id(
                publishing_account_id
            )
        except CredentialEncryptionError:
            raise OAuthCredentialUnavailableError from None
        if credential is None:
            raise OAuthCredentialUnavailableError

        now = self._clock()
        if credential.expires_at is None or credential.expires_at > now + self.REFRESH_SKEW:
            return ResolvedOAuthCredential(
                access_token=credential.access_token,
                token_type=credential.token_type,
                expires_at=credential.expires_at,
                scope=credential.scope,
            )
        if credential.refresh_token is None:
            raise OAuthCredentialRefreshUnavailableError

        try:
            refreshed = await self._refresh_provider.refresh_token(
                refresh_token=credential.refresh_token
            )
        except OAuthTokenRefreshRateLimitError as error:
            raise PublishingRateLimitError(retry_after_seconds=error.retry_after_seconds) from None
        except OAuthTokenRefreshAuthenticationError:
            raise PublishingAuthenticationError from None
        except OAuthTokenRefreshTransientError:
            raise PublishingTransientError from None
        except OAuthTokenRefreshError:
            raise OAuthCredentialRefreshFailedError from None

        expires_at = (
            now + timedelta(seconds=refreshed.expires_in)
            if refreshed.expires_in is not None
            else None
        )
        token_type = refreshed.token_type or credential.token_type
        scope = refreshed.scope or credential.scope
        try:
            await self._credential_service.store(
                publishing_account_id,
                OAuthCredentialInput(
                    access_token=refreshed.access_token,
                    refresh_token=refreshed.refresh_token,
                    token_type=token_type,
                    scope=scope,
                    expires_at=expires_at,
                ),
            )
        except CredentialEncryptionError:
            raise OAuthCredentialPersistenceError from None
        return ResolvedOAuthCredential(
            access_token=refreshed.access_token,
            token_type=token_type,
            expires_at=expires_at,
            scope=scope,
        )
