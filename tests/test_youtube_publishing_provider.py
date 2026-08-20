import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing import (
    PublishingProvider,
    YouTubePublishingError,
    YouTubePublishingProvider,
)
from app.schemas.publish_job import PublishingInput, PublishingResult

ACCESS_TOKEN = "clipforge-youtube-upload-test-access-token-never-real"
VIDEO_BYTES = b"clipforge-test-video-bytes"
VIDEO_ID = "youtube-test-video-id"
STORAGE_KEY = "renders/4/output.mp4"
ACCOUNT_REFERENCE = "channel-main-not-a-database-id"
NOW = datetime(2030, 1, 1, tzinfo=UTC)
BOUNDARY = "clipforge-test-boundary"


def publishing_input(**values) -> PublishingInput:
    defaults = {
        "publish_job_id": 5,
        "video_render_id": 4,
        "platform": "youtube",
        "account_reference": ACCOUNT_REFERENCE,
        "source_storage_key": STORAGE_KEY,
        "source_checksum": "sha256:abc",
        "source_file_size_bytes": len(VIDEO_BYTES),
        "source_duration_seconds": 15,
        "title": "Publish deliberately",
        "description": "A useful Short.",
        "tags": ["Editing", "Shorts"],
        "category": "education",
        "visibility": "private",
        "made_for_kids": False,
        "notify_subscribers": False,
        "language": "en",
        "recording_date": None,
        "scheduled_publish_at": None,
        "publish_options": {
            "platform": "youtube",
            "account_reference": ACCOUNT_REFERENCE,
            "visibility": "private",
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": None,
        },
    }
    defaults.update(values)
    return PublishingInput.model_validate(defaults)


class FakeCredentialResolver:
    def __init__(self, *, error: Exception | None = None, token_type="Bearer") -> None:
        self.error = error
        self.token_type = token_type
        self.references: list[str] = []

    async def resolve(self, account_reference: str):
        self.references.append(account_reference)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(access_token=ACCESS_TOKEN, token_type=self.token_type)


class FakeArtifactReader:
    def __init__(self, *, error: Exception | None = None, content=VIDEO_BYTES) -> None:
        self.error = error
        self.content = content
        self.keys: list[str] = []

    async def read(self, storage_key: str):
        self.keys.append(storage_key)
        if self.error is not None:
            raise self.error
        return self.content


def make_provider(handler, *, resolver=None, reader=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30.0)
    credential_resolver = resolver or FakeCredentialResolver()
    artifact_reader = reader or FakeArtifactReader()
    provider = YouTubePublishingProvider(
        credential_resolver,
        artifact_reader,
        client,
        boundary_factory=lambda: BOUNDARY,
        clock=lambda: NOW,
    )
    return provider, client, credential_resolver, artifact_reader


def parse_multipart(request: httpx.Request) -> tuple[dict, bytes]:
    parts = request.content.split(f"--{BOUNDARY}".encode())
    metadata_part = parts[1].strip(b"\r\n")
    media_part = parts[2].strip(b"\r\n")
    metadata_headers, metadata_body = metadata_part.split(b"\r\n\r\n", 1)
    media_headers, media_body = media_part.split(b"\r\n\r\n", 1)
    assert metadata_headers == b"Content-Type: application/json; charset=UTF-8"
    assert media_headers == b"Content-Type: video/mp4"
    return json.loads(metadata_body), media_body


def test_provider_structurally_implements_existing_async_contract() -> None:
    provider, _, _, _ = make_provider(lambda _: httpx.Response(200, json={"id": VIDEO_ID}))
    boundary: PublishingProvider = provider

    assert boundary is provider
    assert inspect.iscoroutinefunction(provider.publish)


async def test_success_builds_youtube_multipart_request_and_safe_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url.copy_with(query=None)) == (
            "https://www.googleapis.com/upload/youtube/v3/videos"
        )
        assert parse_qs(request.url.query.decode()) == {
            "part": ["snippet,status"],
            "uploadType": ["multipart"],
            "notifySubscribers": ["false"],
        }
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert request.headers["content-type"] == (f'multipart/related; boundary="{BOUNDARY}"')
        metadata, media = parse_multipart(request)
        assert metadata == {
            "snippet": {
                "title": "Publish deliberately",
                "description": "A useful Short.",
                "tags": ["Editing", "Shorts"],
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
            },
        }
        assert "categoryId" not in metadata["snippet"]
        assert media == VIDEO_BYTES
        return httpx.Response(200, json={"id": VIDEO_ID})

    provider, client, resolver, reader = make_provider(handler)
    async with client:
        result = await provider.publish(publishing_input())

    assert resolver.references == [ACCOUNT_REFERENCE]
    assert reader.keys == [STORAGE_KEY]
    assert PublishingResult.model_validate(result) == result
    assert result.remote_media_id == VIDEO_ID
    assert str(result.remote_url) == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert result.remote_status == "published"
    assert result.published_at == NOW
    assert result.provider_metadata == {
        "provider": "youtube",
        "privacy_status": "private",
        "upload_type": "multipart",
        "video_id": VIDEO_ID,
    }
    assert ACCESS_TOKEN not in json.dumps(result.model_dump(mode="json"))


@pytest.mark.parametrize(
    "visibility",
    [PublishVisibility.PUBLIC, PublishVisibility.PRIVATE, PublishVisibility.UNLISTED],
)
async def test_all_privacy_values_map_exactly(visibility: PublishVisibility) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_multipart(request)[0])
        return httpx.Response(200, json={"id": VIDEO_ID})

    provider, client, _, _ = make_provider(handler)
    async with client:
        await provider.publish(publishing_input(visibility=visibility))

    assert captured["status"]["privacyStatus"] == visibility.value


@pytest.mark.parametrize("token_type", [None, "Bearer", "bearer"])
async def test_bearer_or_unspecified_token_type_is_supported(token_type) -> None:
    provider, client, _, _ = make_provider(
        lambda _: httpx.Response(200, json={"id": VIDEO_ID}),
        resolver=FakeCredentialResolver(token_type=token_type),
    )

    async with client:
        result = await provider.publish(publishing_input())

    assert result.remote_media_id == VIDEO_ID


async def test_unsupported_token_type_fails_before_artifact_or_http() -> None:
    reader = FakeArtifactReader()
    provider, client, _, _ = make_provider(
        lambda _: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
        resolver=FakeCredentialResolver(token_type="MAC"),
        reader=reader,
    )

    async with client:
        with pytest.raises(YouTubePublishingError):
            await provider.publish(publishing_input())

    assert reader.keys == []


async def test_credential_failure_prevents_artifact_read_and_http_without_leakage() -> None:
    reader = FakeArtifactReader()
    provider, client, _, _ = make_provider(
        lambda _: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
        resolver=FakeCredentialResolver(error=RuntimeError(ACCESS_TOKEN)),
        reader=reader,
    )

    async with client:
        with pytest.raises(YouTubePublishingError) as error:
            await provider.publish(publishing_input())

    assert reader.keys == []
    assert ACCESS_TOKEN not in str(error.value)
    assert ACCESS_TOKEN not in repr(error.value)


async def test_artifact_failure_prevents_http_and_is_normalized() -> None:
    provider, client, _, _ = make_provider(
        lambda _: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
        reader=FakeArtifactReader(error=OSError("test artifact path")),
    )

    async with client:
        with pytest.raises(YouTubePublishingError) as error:
            await provider.publish(publishing_input())

    assert "artifact path" not in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "connection"])
async def test_transport_failures_are_normalized_without_token_leakage(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        raise httpx.ConnectError("test connection", request=request)

    provider, client, _, _ = make_provider(handler)
    async with client:
        with pytest.raises(YouTubePublishingError) as error:
            await provider.publish(publishing_input())

    assert ACCESS_TOKEN not in str(error.value)
    assert ACCESS_TOKEN not in repr(error.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": ACCESS_TOKEN}}),
        httpx.Response(403, json={"error": "quota"}),
        httpx.Response(500, content=b"remote failure"),
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"id": ""}),
        httpx.Response(200, json=[]),
    ],
)
async def test_remote_and_response_failures_are_safe(response: httpx.Response) -> None:
    provider, client, _, _ = make_provider(lambda _: response)

    async with client:
        with pytest.raises(YouTubePublishingError) as error:
            await provider.publish(publishing_input())

    assert str(error.value) == "YouTube publishing failed"
    assert ACCESS_TOKEN not in repr(error.value)


async def test_non_youtube_platform_is_rejected_before_dependencies() -> None:
    resolver = FakeCredentialResolver()
    reader = FakeArtifactReader()
    provider, client, _, _ = make_provider(
        lambda _: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
        resolver=resolver,
        reader=reader,
    )
    unchecked = publishing_input().model_copy(update={"platform": PublishPlatform.OTHER})

    async with client:
        with pytest.raises(YouTubePublishingError):
            await provider.publish(unchecked)

    assert resolver.references == []
    assert reader.keys == []
