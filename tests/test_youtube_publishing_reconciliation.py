from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.exceptions import (
    PublishingAuthenticationError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing import (
    PublishingReconciliationInput,
    PublishingRemoteState,
    ResumablePublishingSession,
    YouTubePublishingError,
    YouTubePublishingProvider,
)

ACCESS_TOKEN = "reconciliation-access-token-never-real"
SESSION_URI = "https://upload.youtube.test/reconciliation-secret-session"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class CredentialResolver:
    def __init__(self) -> None:
        self.references = []

    async def resolve(self, account_reference: str):
        self.references.append(account_reference)
        return SimpleNamespace(access_token=ACCESS_TOKEN, token_type="Bearer")


class ForbiddenArtifactReader:
    async def read(self, storage_key: str):
        raise AssertionError("reconciliation must not read artifact bytes")


def reconciliation_input(**values) -> PublishingReconciliationInput:
    defaults = {
        "platform": PublishPlatform.YOUTUBE,
        "account_reference": "channel-main",
        "visibility": PublishVisibility.PRIVATE,
        "remote_media_id": "youtube-video-123",
    }
    defaults.update(values)
    return PublishingReconciliationInput(**defaults)


def make_provider(handler):
    resolver = CredentialResolver()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YouTubePublishingProvider(
        resolver,
        ForbiddenArtifactReader(),
        client,
        clock=lambda: NOW,
    )
    return provider, client, resolver


async def test_remote_media_query_maps_existing_video_without_artifact_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url.copy_with(query=None)) == (
            "https://www.googleapis.com/youtube/v3/videos"
        )
        assert parse_qs(request.url.query.decode()) == {
            "part": ["status,snippet"],
            "id": ["youtube-video-123"],
        }
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        return httpx.Response(
            200,
            json={"items": [{"id": "youtube-video-123", "status": {"uploadStatus": "processed"}}]},
        )

    provider, client, resolver = make_provider(handler)
    async with client:
        result = await provider.reconcile(reconciliation_input())

    assert resolver.references == ["channel-main"]
    assert result.remote_state is PublishingRemoteState.PUBLISHED
    assert result.publishing_result is not None
    assert result.publishing_result.remote_media_id == "youtube-video-123"
    assert result.publishing_result.remote_status == "processed"
    assert result.publishing_result.published_at == NOW
    assert result.publishing_result.provider_metadata["reconciled"] is True
    assert ACCESS_TOKEN not in repr(result)


async def test_remote_media_query_empty_items_is_not_found() -> None:
    provider, client, _ = make_provider(lambda _: httpx.Response(200, json={"items": []}))
    async with client:
        result = await provider.reconcile(reconciliation_input())
    assert result.remote_state is PublishingRemoteState.NOT_FOUND
    assert result.publishing_result is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": [{}]},
        {"items": "invalid"},
        {"items": [{"id": "unexpected-video"}]},
    ],
)
async def test_remote_media_query_rejects_malformed_success_payload(payload) -> None:
    provider, client, _ = make_provider(lambda _: httpx.Response(200, json=payload))
    async with client:
        with pytest.raises(YouTubePublishingError):
            await provider.reconcile(reconciliation_input())


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            httpx.Response(401, json={"error": {"message": "secret raw message"}}),
            PublishingAuthenticationError,
        ),
        (httpx.Response(429, headers={"Retry-After": "45"}), PublishingRateLimitError),
        (httpx.Response(503, content=b"secret raw body"), PublishingTransientError),
    ],
)
async def test_remote_media_query_reuses_safe_error_taxonomy(response, error_type) -> None:
    provider, client, _ = make_provider(lambda _: response)
    async with client:
        with pytest.raises(error_type) as captured:
            await provider.reconcile(reconciliation_input())
    assert "secret raw" not in str(captured.value)
    if isinstance(captured.value, PublishingRateLimitError):
        assert captured.value.retry_after_seconds == 45


async def test_session_probe_incomplete_returns_authoritative_offset_without_body() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(308, headers={"Range": "bytes=0-499"})

    provider, client, _ = make_provider(handler)
    session = ResumablePublishingSession(SESSION_URI, 1000, 100)
    async with client:
        result = await provider.reconcile(
            reconciliation_input(remote_media_id=None, resumable_session=session)
        )

    request = requests[0]
    assert request.method == "PUT"
    assert str(request.url) == SESSION_URI
    assert request.headers["content-length"] == "0"
    assert request.headers["content-range"] == "bytes */1000"
    assert request.content == b""
    assert result.remote_state is PublishingRemoteState.INCOMPLETE
    assert result.next_byte_offset == 500
    assert SESSION_URI not in repr(result)


async def test_session_probe_completed_maps_published_result() -> None:
    provider, client, _ = make_provider(
        lambda _: httpx.Response(200, json={"id": "recovered-video-123"})
    )
    session = ResumablePublishingSession(SESSION_URI, 1000)
    async with client:
        result = await provider.reconcile(
            reconciliation_input(remote_media_id=None, resumable_session=session)
        )
    assert result.remote_state is PublishingRemoteState.PUBLISHED
    assert result.publishing_result is not None
    assert result.publishing_result.remote_media_id == "recovered-video-123"


async def test_session_probe_404_is_non_destructive_not_found_result() -> None:
    provider, client, _ = make_provider(lambda _: httpx.Response(404))
    async with client:
        result = await provider.reconcile(
            reconciliation_input(
                remote_media_id=None,
                resumable_session=ResumablePublishingSession(SESSION_URI, 1000),
            )
        )
    assert result.remote_state is PublishingRemoteState.NOT_FOUND


async def test_session_probe_malformed_range_is_safe_provider_error() -> None:
    provider, client, _ = make_provider(
        lambda _: httpx.Response(308, headers={"Range": "private-invalid-range"})
    )
    async with client:
        with pytest.raises(YouTubePublishingError) as captured:
            await provider.reconcile(
                reconciliation_input(
                    remote_media_id=None,
                    resumable_session=ResumablePublishingSession(SESSION_URI, 1000),
                )
            )
    assert SESSION_URI not in str(captured.value)
