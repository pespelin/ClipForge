from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import (
    OAuthCredentialPersistenceError,
    OAuthCredentialRefreshFailedError,
    OAuthCredentialRefreshUnavailableError,
    OAuthCredentialUnavailableError,
)
from app.providers.oauth import OAuthTokenRefreshError, OAuthTokenResult
from app.security import CredentialEncryptionError
from app.services.oauth_credential_resolver import (
    OAuthCredentialResolver,
    ResolvedOAuthCredential,
)
from app.services.oauth_credential_service import OAuthCredentialData

NOW = datetime(2030, 1, 1, tzinfo=UTC)
ACCESS_TOKEN = "clipforge-test-access-token-never-real"
REFRESH_TOKEN = "clipforge-test-refresh-token-never-real"
NEW_ACCESS_TOKEN = "clipforge-test-new-access-token-never-real"
ROTATED_REFRESH_TOKEN = "clipforge-test-rotated-refresh-token-never-real"


class FakeCredentialService:
    def __init__(self, credential=None, *, read_error=None, store_error=None) -> None:
        self.credential = credential
        self.read_error = read_error
        self.store_error = store_error
        self.reads: list[int] = []
        self.stores = []

    async def get_decrypted_by_publishing_account_id(self, account_id: int):
        self.reads.append(account_id)
        if self.read_error is not None:
            raise self.read_error
        return self.credential

    async def store(self, account_id: int, credential_input):
        if self.store_error is not None:
            raise self.store_error
        self.stores.append((account_id, credential_input))


class FakeRefreshProvider:
    def __init__(self, result=None, *, fail: bool = False) -> None:
        self.result = result or OAuthTokenResult(access_token=NEW_ACCESS_TOKEN)
        self.fail = fail
        self.calls: list[str] = []

    async def refresh_token(self, *, refresh_token: str):
        self.calls.append(refresh_token)
        if self.fail:
            raise OAuthTokenRefreshError
        return self.result


def credential(
    *,
    expires_at=...,
    refresh_token: str | None = REFRESH_TOKEN,
) -> OAuthCredentialData:
    return OAuthCredentialData(
        publishing_account_id=7,
        access_token=ACCESS_TOKEN,
        refresh_token=refresh_token,
        token_type="Bearer",
        scope="youtube.upload",
        expires_at=NOW + timedelta(hours=1) if expires_at is ... else expires_at,
    )


def make_resolver(credential_service, provider=None) -> tuple[OAuthCredentialResolver, object]:
    refresh_provider = provider or FakeRefreshProvider()
    return (
        OAuthCredentialResolver(
            credential_service,  # type: ignore[arg-type]
            refresh_provider,
            clock=lambda: NOW,
        ),
        refresh_provider,
    )


@pytest.mark.parametrize("expires_at", [NOW + timedelta(minutes=2), None])
async def test_valid_or_unknown_expiry_returns_existing_token_without_refresh(expires_at) -> None:
    credentials = FakeCredentialService(credential(expires_at=expires_at))
    resolver, provider = make_resolver(credentials)

    result = await resolver.resolve(7)

    assert result.access_token == ACCESS_TOKEN
    assert result.token_type == "Bearer"
    assert result.expires_at == expires_at
    assert provider.calls == []  # type: ignore[attr-defined]
    assert credentials.stores == []
    assert ACCESS_TOKEN not in repr(result)


@pytest.mark.parametrize(
    "expires_at",
    [NOW - timedelta(seconds=1), NOW, NOW + timedelta(seconds=60)],
)
async def test_expired_or_within_skew_refreshes(expires_at) -> None:
    credentials = FakeCredentialService(credential(expires_at=expires_at))
    provider = FakeRefreshProvider(OAuthTokenResult(access_token=NEW_ACCESS_TOKEN, expires_in=3600))
    resolver, _ = make_resolver(credentials, provider)

    result = await resolver.resolve(7)

    assert provider.calls == [REFRESH_TOKEN]
    assert result.access_token == NEW_ACCESS_TOKEN
    assert result.expires_at == NOW + timedelta(hours=1)


async def test_due_credential_without_refresh_token_requires_reconnect() -> None:
    credentials = FakeCredentialService(credential(expires_at=NOW, refresh_token=None))
    resolver, provider = make_resolver(credentials)

    with pytest.raises(OAuthCredentialRefreshUnavailableError):
        await resolver.resolve(7)

    assert provider.calls == []  # type: ignore[attr-defined]
    assert credentials.stores == []


async def test_refresh_persists_rotation_expiry_and_preserves_missing_metadata() -> None:
    credentials = FakeCredentialService(credential(expires_at=NOW))
    provider = FakeRefreshProvider(
        OAuthTokenResult(
            access_token=NEW_ACCESS_TOKEN,
            refresh_token=ROTATED_REFRESH_TOKEN,
            expires_in=1800,
        )
    )
    resolver, _ = make_resolver(credentials, provider)

    result = await resolver.resolve(7)

    account_id, persisted = credentials.stores[0]
    assert account_id == 7
    assert persisted.access_token == NEW_ACCESS_TOKEN
    assert persisted.refresh_token == ROTATED_REFRESH_TOKEN
    assert persisted.token_type == "Bearer"
    assert persisted.scope == "youtube.upload"
    assert persisted.expires_at == NOW + timedelta(minutes=30)
    assert result.token_type == "Bearer"
    assert result.scope == "youtube.upload"


async def test_missing_rotated_refresh_token_is_forwarded_as_none_for_preservation() -> None:
    credentials = FakeCredentialService(credential(expires_at=NOW))
    provider = FakeRefreshProvider(
        OAuthTokenResult(access_token=NEW_ACCESS_TOKEN, refresh_token=None)
    )
    resolver, _ = make_resolver(credentials, provider)

    await resolver.resolve(7)

    assert credentials.stores[0][1].refresh_token is None


async def test_refresh_provider_failure_prevents_persistence_and_hides_secret() -> None:
    credentials = FakeCredentialService(credential(expires_at=NOW))
    resolver, _ = make_resolver(credentials, FakeRefreshProvider(fail=True))

    with pytest.raises(OAuthCredentialRefreshFailedError) as error:
        await resolver.resolve(7)

    assert credentials.stores == []
    assert REFRESH_TOKEN not in repr(error.value)


@pytest.mark.parametrize(
    "service",
    [
        FakeCredentialService(None),
        FakeCredentialService(read_error=CredentialEncryptionError()),
    ],
)
async def test_missing_or_undecryptable_credential_is_unavailable(service) -> None:
    resolver, provider = make_resolver(service)

    with pytest.raises(OAuthCredentialUnavailableError):
        await resolver.resolve(7)

    assert provider.calls == []  # type: ignore[attr-defined]


async def test_persistence_encryption_failure_is_safe() -> None:
    credentials = FakeCredentialService(
        credential(expires_at=NOW), store_error=CredentialEncryptionError()
    )
    resolver, _ = make_resolver(credentials)

    with pytest.raises(OAuthCredentialPersistenceError) as error:
        await resolver.resolve(7)

    assert NEW_ACCESS_TOKEN not in repr(error.value)


def test_resolved_credential_repr_hides_access_token() -> None:
    result = ResolvedOAuthCredential(access_token=ACCESS_TOKEN)

    assert ACCESS_TOKEN not in repr(result)
