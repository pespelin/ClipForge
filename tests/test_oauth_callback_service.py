from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

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
from app.providers.oauth import OAuthTokenExchangeError, OAuthTokenResult
from app.security import CredentialEncryptionError, OAuthAuthorizationState, OAuthStateStoreError
from app.services.oauth_callback_service import OAuthCallbackService

STATE = "clipforge-test-state-never-real"
CODE = "clipforge-test-code-never-real"
VERIFIER = "clipforge-test-verifier-never-real"
ACCESS_TOKEN = "clipforge-test-access-token-never-real"
REFRESH_TOKEN = "clipforge-test-refresh-token-never-real"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeStateStore:
    def __init__(self, record=None, *, fail: bool = False) -> None:
        self.record = record
        self.fail = fail
        self.consumed: list[str] = []

    async def save(self, record, ttl_seconds):
        raise AssertionError("callback must not restore state")

    async def consume(self, state: str):
        self.consumed.append(state)
        if self.fail:
            raise OAuthStateStoreError
        record, self.record = self.record, None
        return record


class FakeAccountRepository:
    def __init__(self, account) -> None:
        self.account = account
        self.requested: list[int] = []

    async def get_by_id(self, account_id: int):
        self.requested.append(account_id)
        return self.account


class FakeTokenProvider:
    def __init__(self, token=None, *, fail: bool = False) -> None:
        self.token = token or OAuthTokenResult(access_token=ACCESS_TOKEN)
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def exchange_code(self, *, authorization_code: str, code_verifier: str):
        self.calls.append((authorization_code, code_verifier))
        if self.fail:
            raise OAuthTokenExchangeError
        return self.token


class FakeCredentialService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    async def store(self, account_id, credential_input):
        if self.fail:
            raise CredentialEncryptionError
        self.calls.append((account_id, credential_input))


def account(*, active: bool = True, platform=PublishPlatform.YOUTUBE):
    return SimpleNamespace(id=7, is_active=active, platform=platform)


def record() -> OAuthAuthorizationState:
    return OAuthAuthorizationState(state=STATE, code_verifier=VERIFIER, publishing_account_id=7)


def make_service(
    *,
    state_store=None,
    current_account=...,
    token_provider=None,
    credential_service=None,
):
    store = state_store or FakeStateStore(record())
    repository = FakeAccountRepository(account() if current_account is ... else current_account)
    provider = token_provider or FakeTokenProvider()
    credentials = credential_service or FakeCredentialService()
    service = OAuthCallbackService(
        repository,  # type: ignore[arg-type]
        store,
        provider,
        credentials,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return service, store, repository, provider, credentials


async def test_callback_success_consumes_state_exchanges_and_persists_with_expiry() -> None:
    provider = FakeTokenProvider(
        OAuthTokenResult(
            access_token=ACCESS_TOKEN,
            refresh_token=REFRESH_TOKEN,
            token_type="Bearer",
            scope="youtube.upload",
            expires_in=3600,
        )
    )
    service, store, repository, _, credentials = make_service(token_provider=provider)

    result = await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert result.publishing_account_id == 7
    assert result.connected is True
    assert store.consumed == [STATE]
    assert repository.requested == [7]
    assert provider.calls == [(CODE, VERIFIER)]
    account_id, credential_input = credentials.calls[0]
    assert account_id == 7
    assert credential_input.access_token == ACCESS_TOKEN
    assert credential_input.refresh_token == REFRESH_TOKEN
    assert credential_input.expires_at == datetime(2030, 1, 1, 1, tzinfo=UTC)


async def test_replayed_or_expired_state_is_rejected_before_exchange() -> None:
    store = FakeStateStore(record())
    service, _, _, provider, _ = make_service(state_store=store)
    await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    with pytest.raises(OAuthInvalidStateError):
        await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert provider.calls == [(CODE, VERIFIER)]


@pytest.mark.parametrize("state", [None, ""])
async def test_missing_state_does_not_consume_or_exchange(state: str | None) -> None:
    service, store, _, provider, _ = make_service()

    with pytest.raises(OAuthCallbackValidationError):
        await service.complete(state=state, authorization_code=CODE, provider_error=None)

    assert store.consumed == []
    assert provider.calls == []


async def test_consent_denied_consumes_valid_state_without_exchange() -> None:
    service, store, _, provider, credentials = make_service()

    with pytest.raises(OAuthConsentDeniedError):
        await service.complete(state=STATE, authorization_code=None, provider_error="access_denied")

    assert store.consumed == [STATE]
    assert provider.calls == []
    assert credentials.calls == []


async def test_consent_denied_with_invalid_state_prioritizes_invalid_state() -> None:
    service, _, _, provider, _ = make_service(state_store=FakeStateStore(None))

    with pytest.raises(OAuthInvalidStateError):
        await service.complete(state=STATE, authorization_code=None, provider_error="access_denied")

    assert provider.calls == []


async def test_missing_code_consumes_state_without_exchange() -> None:
    service, store, _, provider, _ = make_service()

    with pytest.raises(OAuthCallbackValidationError):
        await service.complete(state=STATE, authorization_code=None, provider_error=None)

    assert store.consumed == [STATE]
    assert provider.calls == []


@pytest.mark.parametrize(
    ("current_account", "expected_error"),
    [
        (None, PublishingAccountNotFoundError),
        (account(active=False), PublishingAccountInactiveError),
        (account(platform=PublishPlatform.OTHER), UnsupportedOAuthPublishingPlatformError),
    ],
)
async def test_invalid_account_prevents_exchange(current_account, expected_error) -> None:
    service, _, _, provider, _ = make_service(current_account=current_account)

    with pytest.raises(expected_error):
        await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert provider.calls == []


async def test_state_store_failure_is_safe_and_does_not_exchange() -> None:
    service, _, _, provider, _ = make_service(state_store=FakeStateStore(fail=True))

    with pytest.raises(OAuthAuthorizationUnavailableError):
        await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert provider.calls == []


async def test_token_exchange_failure_does_not_persist_or_restore_state() -> None:
    store = FakeStateStore(record())
    service, _, _, provider, credentials = make_service(
        state_store=store, token_provider=FakeTokenProvider(fail=True)
    )

    with pytest.raises(OAuthTokenExchangeFailedError):
        await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert provider.calls == [(CODE, VERIFIER)]
    assert credentials.calls == []
    assert store.record is None


async def test_missing_refresh_token_is_forwarded_as_none_for_preservation() -> None:
    provider = FakeTokenProvider(OAuthTokenResult(access_token=ACCESS_TOKEN, refresh_token=None))
    service, _, _, _, credentials = make_service(token_provider=provider)

    await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert credentials.calls[0][1].refresh_token is None
    assert credentials.calls[0][1].expires_at is None


async def test_credential_encryption_failure_is_safe_and_state_remains_consumed() -> None:
    store = FakeStateStore(record())
    service, _, _, _, _ = make_service(
        state_store=store, credential_service=FakeCredentialService(fail=True)
    )

    with pytest.raises(OAuthCredentialPersistenceError) as error:
        await service.complete(state=STATE, authorization_code=CODE, provider_error=None)

    assert ACCESS_TOKEN not in repr(error.value)
    assert store.record is None
