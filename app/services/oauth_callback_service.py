from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.exceptions import (
    OAuthAuthorizationUnavailableError,
    OAuthCallbackValidationError,
    OAuthConsentDeniedError,
    OAuthCredentialPersistenceError,
    OAuthInvalidStateError,
    OAuthTokenExchangeFailedError,
    PublishingAccountInactiveError,
    PublishingAccountNotFoundError,
    UnsupportedOAuthPublishingPlatformError,
)
from app.models.publish_job import PublishPlatform
from app.providers.oauth import OAuthTokenExchangeError, OAuthTokenExchangeProvider
from app.repositories.publishing_account_repository import PublishingAccountRepository
from app.security import (
    CredentialEncryptionError,
    OAuthAuthorizationStateStore,
    OAuthStateStoreError,
)
from app.services.oauth_credential_service import OAuthCredentialInput, OAuthCredentialService


@dataclass(frozen=True, slots=True)
class OAuthCallbackResult:
    publishing_account_id: int
    connected: bool = True


class OAuthCallbackService:
    def __init__(
        self,
        account_repository: PublishingAccountRepository,
        state_store: OAuthAuthorizationStateStore,
        token_exchange_provider: OAuthTokenExchangeProvider,
        credential_service: OAuthCredentialService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._account_repository = account_repository
        self._state_store = state_store
        self._token_exchange_provider = token_exchange_provider
        self._credential_service = credential_service
        self._clock = clock

    async def complete(
        self,
        *,
        state: str | None,
        authorization_code: str | None,
        provider_error: str | None,
    ) -> OAuthCallbackResult:
        if not state:
            raise OAuthCallbackValidationError
        try:
            record = await self._state_store.consume(state)
        except OAuthStateStoreError:
            raise OAuthAuthorizationUnavailableError from None
        if record is None:
            raise OAuthInvalidStateError
        if provider_error is not None:
            raise OAuthConsentDeniedError
        if not authorization_code:
            raise OAuthCallbackValidationError

        account = await self._account_repository.get_by_id(record.publishing_account_id)
        if account is None:
            raise PublishingAccountNotFoundError
        if not account.is_active:
            raise PublishingAccountInactiveError
        if account.platform != PublishPlatform.YOUTUBE:
            raise UnsupportedOAuthPublishingPlatformError

        try:
            token = await self._token_exchange_provider.exchange_code(
                authorization_code=authorization_code,
                code_verifier=record.code_verifier,
            )
        except OAuthTokenExchangeError:
            raise OAuthTokenExchangeFailedError from None

        expires_at = (
            self._clock() + timedelta(seconds=token.expires_in)
            if token.expires_in is not None
            else None
        )
        try:
            await self._credential_service.store(
                account.id,
                OAuthCredentialInput(
                    access_token=token.access_token,
                    refresh_token=token.refresh_token,
                    token_type=token.token_type,
                    scope=token.scope,
                    expires_at=expires_at,
                ),
            )
        except CredentialEncryptionError:
            raise OAuthCredentialPersistenceError from None
        return OAuthCallbackResult(publishing_account_id=account.id)
