from collections.abc import Callable
from dataclasses import dataclass

from app.core.exceptions import (
    OAuthAuthorizationConfigurationError,
    OAuthAuthorizationUnavailableError,
    PublishingAccountInactiveError,
    PublishingAccountNotFoundError,
    UnsupportedOAuthPublishingPlatformError,
)
from app.models.publish_job import PublishPlatform
from app.providers.oauth import OAuthAuthorizationProvider
from app.repositories.publishing_account_repository import PublishingAccountRepository
from app.security import (
    OAuthAuthorizationState,
    OAuthAuthorizationStateStore,
    OAuthStateStoreError,
    create_pkce_code_challenge,
    generate_oauth_state,
    generate_pkce_code_verifier,
)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


@dataclass(frozen=True, slots=True)
class OAuthAuthorizationResult:
    authorization_url: str


class OAuthAuthorizationService:
    def __init__(
        self,
        account_repository: PublishingAccountRepository,
        state_store: OAuthAuthorizationStateStore,
        authorization_provider: OAuthAuthorizationProvider,
        *,
        client_id: str | None,
        redirect_uri: str | None,
        state_ttl_seconds: int,
        state_generator: Callable[[], str] = generate_oauth_state,
        verifier_generator: Callable[[], str] = generate_pkce_code_verifier,
        challenge_builder: Callable[[str], str] = create_pkce_code_challenge,
    ) -> None:
        self._account_repository = account_repository
        self._state_store = state_store
        self._authorization_provider = authorization_provider
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._state_ttl_seconds = state_ttl_seconds
        self._state_generator = state_generator
        self._verifier_generator = verifier_generator
        self._challenge_builder = challenge_builder

    async def authorize(self, publishing_account_id: int) -> OAuthAuthorizationResult:
        account = await self._account_repository.get_by_id(publishing_account_id)
        if account is None:
            raise PublishingAccountNotFoundError
        if not account.is_active:
            raise PublishingAccountInactiveError
        if account.platform != PublishPlatform.YOUTUBE:
            raise UnsupportedOAuthPublishingPlatformError
        if not self._client_id or not self._redirect_uri:
            raise OAuthAuthorizationConfigurationError

        state = self._state_generator()
        code_verifier = self._verifier_generator()
        code_challenge = self._challenge_builder(code_verifier)
        record = OAuthAuthorizationState(
            state=state,
            code_verifier=code_verifier,
            publishing_account_id=account.id,
        )
        try:
            await self._state_store.save(record, self._state_ttl_seconds)
        except OAuthStateStoreError:
            raise OAuthAuthorizationUnavailableError from None

        authorization_url = self._authorization_provider.build_authorization_url(
            client_id=self._client_id,
            redirect_uri=self._redirect_uri,
            state=state,
            code_challenge=code_challenge,
            scopes=(YOUTUBE_UPLOAD_SCOPE,),
        )
        return OAuthAuthorizationResult(authorization_url=authorization_url)
