from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.publish_job import PublishPlatform
from app.providers.publishing import (
    FilesystemPublishingArtifactReader,
    LocalPublishingProvider,
    OAuthPublishingCredentialResolver,
    PublishingArtifactReadError,
    PublishingCredentialResolutionError,
    PublishingProviderConfigurationError,
    YouTubePublishingProvider,
    create_publishing_composition,
)

FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def youtube_settings(**values) -> Settings:
    defaults = {
        "publishing_provider": "youtube",
        "youtube_oauth_client_id": "test-client-id",
        "youtube_oauth_client_secret": SecretStr("test-client-secret"),
        "credential_encryption_key": SecretStr(FERNET_KEY),
        "storage_root": "test-storage",
    }
    defaults.update(values)
    return Settings(**defaults)


def test_local_composition_requires_no_youtube_dependencies_or_secrets() -> None:
    composition = create_publishing_composition(settings=Settings())

    assert isinstance(composition.provider, LocalPublishingProvider)
    assert composition.upload_session_service is None


def test_youtube_composition_wires_provider_and_checkpoint_service() -> None:
    session = AsyncMock(spec=AsyncSession)
    client = Mock(spec=httpx.AsyncClient)

    composition = create_publishing_composition(
        settings=youtube_settings(),
        session=session,
        http_client=client,
    )

    assert isinstance(composition.provider, YouTubePublishingProvider)
    assert isinstance(composition.provider._credential_resolver, OAuthPublishingCredentialResolver)
    bridge = composition.provider._credential_resolver
    assert bridge._account_repository.session is session
    assert bridge._credential_resolver._refresh_provider._client is client
    assert bridge._credential_resolver._credential_service.credential_repository.session is session
    assert isinstance(composition.provider._artifact_reader, FilesystemPublishingArtifactReader)
    assert composition.provider._http_client is client
    assert composition.upload_session_service is not None
    assert composition.upload_session_service.repository.session is session


@pytest.mark.parametrize(
    "changes",
    [
        {"youtube_oauth_client_id": None},
        {"youtube_oauth_client_secret": None},
        {"credential_encryption_key": None},
        {"credential_encryption_key": SecretStr("invalid")},
    ],
)
def test_youtube_composition_rejects_missing_or_invalid_runtime_config(changes) -> None:
    with pytest.raises(PublishingProviderConfigurationError) as error:
        create_publishing_composition(
            settings=youtube_settings(**changes),
            session=AsyncMock(spec=AsyncSession),
            http_client=Mock(spec=httpx.AsyncClient),
        )

    assert "test-client-secret" not in repr(error.value)
    assert FERNET_KEY not in repr(error.value)


async def test_oauth_bridge_maps_exact_remote_reference_to_account_id() -> None:
    account = SimpleNamespace(
        id=17,
        is_active=True,
        platform=PublishPlatform.YOUTUBE,
    )

    class AccountRepository:
        def __init__(self) -> None:
            self.calls = []

        async def get_by_platform_and_remote_account_id(self, platform, remote_id):
            self.calls.append((platform, remote_id))
            return account

    class CredentialResolver:
        def __init__(self) -> None:
            self.ids = []

        async def resolve(self, account_id):
            self.ids.append(account_id)
            return SimpleNamespace(access_token="safe-test-token", token_type="Bearer")

    accounts = AccountRepository()
    credentials = CredentialResolver()
    bridge = OAuthPublishingCredentialResolver(accounts, credentials)

    result = await bridge.resolve("channel-reference-not-an-integer")

    assert accounts.calls == [(PublishPlatform.YOUTUBE, "channel-reference-not-an-integer")]
    assert credentials.ids == [17]
    assert result.access_token == "safe-test-token"


@pytest.mark.parametrize(
    "account",
    [
        None,
        SimpleNamespace(id=17, is_active=False, platform=PublishPlatform.YOUTUBE),
        SimpleNamespace(id=17, is_active=True, platform=PublishPlatform.TIKTOK),
    ],
)
async def test_oauth_bridge_rejects_missing_inactive_or_wrong_platform_account(
    account,
) -> None:
    class AccountRepository:
        async def get_by_platform_and_remote_account_id(self, platform, remote_id):
            return account

    class CredentialResolver:
        async def resolve(self, account_id):
            raise AssertionError("credential lookup must not run")

    with pytest.raises(PublishingCredentialResolutionError):
        await OAuthPublishingCredentialResolver(AccountRepository(), CredentialResolver()).resolve(
            "channel-reference"
        )


async def test_filesystem_artifact_reader_reads_only_inside_storage_root(tmp_path) -> None:
    root = tmp_path / "storage"
    artifact = root / "renders/7/output.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"test-video")
    reader = FilesystemPublishingArtifactReader(root)

    assert await reader.read("renders/7/output.mp4") == b"test-video"

    with pytest.raises(PublishingArtifactReadError):
        await reader.read("../outside.mp4")
