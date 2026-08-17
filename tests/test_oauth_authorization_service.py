from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    OAuthAuthorizationConfigurationError,
    OAuthAuthorizationUnavailableError,
    PublishingAccountInactiveError,
    PublishingAccountNotFoundError,
    UnsupportedOAuthPublishingPlatformError,
)
from app.models.publish_job import PublishPlatform
from app.security import OAuthAuthorizationState, OAuthStateStoreError
from app.services.oauth_authorization_service import (
    YOUTUBE_UPLOAD_SCOPE,
    OAuthAuthorizationService,
)

STATE = "deterministic-test-state"
VERIFIER = "deterministic-test-verifier"
CHALLENGE = "deterministic-test-challenge"


class FakeAccountRepository:
    def __init__(self, account) -> None:
        self.account = account
        self.requested_ids: list[int] = []

    async def get_by_id(self, account_id: int):
        self.requested_ids.append(account_id)
        return self.account


class FakeStateStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.saved: list[tuple[OAuthAuthorizationState, int]] = []

    async def save(self, record: OAuthAuthorizationState, ttl_seconds: int) -> None:
        if self.fail:
            raise OAuthStateStoreError
        self.saved.append((record, ttl_seconds))

    async def consume(self, state: str) -> OAuthAuthorizationState | None:
        return None


class FakeAuthorizationProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def build_authorization_url(self, **parameters) -> str:
        self.calls.append(parameters)
        return "https://accounts.google.test/authorize"


def youtube_account(*, active: bool = True):
    return SimpleNamespace(id=7, platform=PublishPlatform.YOUTUBE, is_active=active)


def make_service(
    account,
    *,
    state_store: FakeStateStore | None = None,
    provider: FakeAuthorizationProvider | None = None,
    client_id: str | None = "clipforge-test-client-id",
    redirect_uri: str | None = "https://clipforge.test/oauth/callback",
):
    store = state_store or FakeStateStore()
    authorization_provider = provider or FakeAuthorizationProvider()
    service = OAuthAuthorizationService(
        FakeAccountRepository(account),  # type: ignore[arg-type]
        store,
        authorization_provider,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state_ttl_seconds=600,
        state_generator=lambda: STATE,
        verifier_generator=lambda: VERIFIER,
        challenge_builder=lambda value: CHALLENGE if value == VERIFIER else "unexpected",
    )
    return service, store, authorization_provider


async def test_authorize_validates_account_saves_state_and_builds_safe_url() -> None:
    service, store, provider = make_service(youtube_account())

    result = await service.authorize(7)

    assert result.authorization_url == "https://accounts.google.test/authorize"
    assert store.saved == [
        (
            OAuthAuthorizationState(
                state=STATE,
                code_verifier=VERIFIER,
                publishing_account_id=7,
            ),
            600,
        )
    ]
    assert provider.calls == [
        {
            "client_id": "clipforge-test-client-id",
            "redirect_uri": "https://clipforge.test/oauth/callback",
            "state": STATE,
            "code_challenge": CHALLENGE,
            "scopes": (YOUTUBE_UPLOAD_SCOPE,),
        }
    ]


@pytest.mark.parametrize(
    ("account", "expected_error"),
    [
        (None, PublishingAccountNotFoundError),
        (youtube_account(active=False), PublishingAccountInactiveError),
        (
            SimpleNamespace(id=7, platform=PublishPlatform.OTHER, is_active=True),
            UnsupportedOAuthPublishingPlatformError,
        ),
    ],
)
async def test_invalid_account_has_no_state_or_provider_side_effects(
    account, expected_error: type[Exception]
) -> None:
    service, store, provider = make_service(account)

    with pytest.raises(expected_error):
        await service.authorize(7)

    assert store.saved == []
    assert provider.calls == []


@pytest.mark.parametrize(
    ("client_id", "redirect_uri"),
    [
        (None, "https://clipforge.test/oauth/callback"),
        ("clipforge-test-client-id", None),
        ("", "https://clipforge.test/oauth/callback"),
    ],
)
async def test_missing_required_config_fails_before_state_is_saved(
    client_id: str | None, redirect_uri: str | None
) -> None:
    service, store, provider = make_service(
        youtube_account(), client_id=client_id, redirect_uri=redirect_uri
    )

    with pytest.raises(OAuthAuthorizationConfigurationError):
        await service.authorize(7)

    assert store.saved == []
    assert provider.calls == []


async def test_state_store_failure_prevents_authorization_success_and_provider_call() -> None:
    store = FakeStateStore(fail=True)
    service, _, provider = make_service(youtube_account(), state_store=store)

    with pytest.raises(OAuthAuthorizationUnavailableError) as error:
        await service.authorize(7)

    assert STATE not in str(error.value)
    assert VERIFIER not in repr(error.value)
    assert provider.calls == []
