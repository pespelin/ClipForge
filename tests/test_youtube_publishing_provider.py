import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.exceptions import PublishingExecutionLeaseLostError, PublishingTransientError
from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing import (
    PublishingProvider,
    YouTubePublishingError,
    YouTubePublishingProvider,
    YouTubeResumableUploadSession,
)
from app.schemas.publish_job import PublishingInput, PublishingResult

ACCESS_TOKEN = "clipforge-youtube-upload-test-access-token-never-real"
SESSION_URI = "https://upload.youtube.test/session/secret-capability-url"
VIDEO_BYTES = b"abcdefghij"
VIDEO_ID = "youtube-test-video-id"
STORAGE_KEY = "renders/4/output.mp4"
ACCOUNT_REFERENCE = "channel-main-not-a-database-id"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


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


class FakeExecutionGuard:
    def __init__(self, events: list[str], *, fail_on_call: int | None = None) -> None:
        self.events = events
        self.fail_on_call = fail_on_call
        self.calls = 0

    async def renew(self) -> None:
        self.calls += 1
        self.events.append(f"renew-{self.calls}")
        if self.calls == self.fail_on_call:
            raise PublishingExecutionLeaseLostError


def make_provider(handler, *, resolver=None, reader=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30.0)
    credential_resolver = resolver or FakeCredentialResolver()
    artifact_reader = reader or FakeArtifactReader()
    provider = YouTubePublishingProvider(
        credential_resolver,
        artifact_reader,
        client,
        clock=lambda: NOW,
    )
    return provider, client, credential_resolver, artifact_reader


def initiation_response() -> httpx.Response:
    return httpx.Response(200, headers={"Location": SESSION_URI})


def completed_response() -> httpx.Response:
    return httpx.Response(200, json={"id": VIDEO_ID})


def test_provider_structurally_implements_existing_async_contract() -> None:
    provider, _, _, _ = make_provider(lambda _: completed_response())
    boundary: PublishingProvider = provider
    assert boundary is provider
    assert inspect.iscoroutinefunction(provider.publish)


async def test_publish_uses_resumable_initiation_and_full_media_put() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert str(request.url.copy_with(query=None)) == (
                "https://www.googleapis.com/upload/youtube/v3/videos"
            )
            assert parse_qs(request.url.query.decode()) == {
                "part": ["snippet,status"],
                "uploadType": ["resumable"],
                "notifySubscribers": ["false"],
            }
            assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
            assert request.headers["content-type"] == "application/json; charset=UTF-8"
            assert request.headers["x-upload-content-type"] == "video/mp4"
            assert request.headers["x-upload-content-length"] == "10"
            assert json.loads(request.content) == {
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
            return initiation_response()
        assert str(request.url) == SESSION_URI
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert request.headers["content-type"] == "video/mp4"
        assert request.headers["content-length"] == "10"
        assert request.headers["content-range"] == "bytes 0-9/10"
        assert request.content == VIDEO_BYTES
        return completed_response()

    provider, client, resolver, reader = make_provider(handler)
    async with client:
        result = await provider.publish(publishing_input())

    assert [request.method for request in requests] == ["POST", "PUT"]
    assert resolver.references == [ACCOUNT_REFERENCE]
    assert reader.keys == [STORAGE_KEY]
    assert PublishingResult.model_validate(result) == result
    assert result.remote_media_id == VIDEO_ID
    assert str(result.remote_url) == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert result.published_at == NOW
    assert result.provider_metadata == {
        "provider": "youtube",
        "privacy_status": "private",
        "upload_type": "resumable",
        "video_id": VIDEO_ID,
    }
    dumped = json.dumps(result.model_dump(mode="json"))
    assert ACCESS_TOKEN not in dumped
    assert SESSION_URI not in dumped


@pytest.mark.parametrize(
    "visibility",
    [PublishVisibility.PUBLIC, PublishVisibility.PRIVATE, PublishVisibility.UNLISTED],
)
async def test_all_privacy_values_map_exactly(visibility: PublishVisibility) -> None:
    metadata = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            metadata.update(json.loads(request.content))
            return initiation_response()
        return completed_response()

    provider, client, _, _ = make_provider(handler)
    async with client:
        result = await provider.publish(publishing_input(visibility=visibility))
    assert metadata["status"]["privacyStatus"] == visibility.value
    assert result.provider_metadata["privacy_status"] == visibility.value


async def test_session_uri_is_secret_safe() -> None:
    session = YouTubeResumableUploadSession(SESSION_URI, 10)
    assert SESSION_URI not in repr(session)

    provider, client, _, _ = make_provider(
        lambda request: (
            initiation_response()
            if request.method == "POST"
            else httpx.Response(500, text=SESSION_URI)
        )
    )
    async with client:
        with pytest.raises(PublishingTransientError) as error:
            await provider.publish(publishing_input())
    assert SESSION_URI not in str(error.value)
    assert SESSION_URI not in repr(error.value)


async def test_existing_session_probes_and_resumes_from_reported_offset_without_post() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PUT"
        if len(requests) == 1:
            assert request.headers["content-length"] == "0"
            assert request.headers["content-range"] == "bytes */10"
            assert request.content == b""
            return httpx.Response(308, headers={"Range": "bytes=0-3"})
        assert request.headers["content-length"] == "6"
        assert request.headers["content-range"] == "bytes 4-9/10"
        assert request.content == b"efghij"
        return completed_response()

    provider, client, _, _ = make_provider(handler)
    session = YouTubeResumableUploadSession(SESSION_URI, 10)
    async with client:
        result = await provider.resume_upload(publishing_input(), session)
    assert [request.method for request in requests] == ["PUT", "PUT"]
    assert result.remote_media_id == VIDEO_ID


async def test_execution_guard_renews_before_probe_and_media_put() -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["content-range"] == "bytes */10":
            events.append("probe")
            return httpx.Response(308, headers={"Range": "bytes=0-3"})
        events.append("media")
        return completed_response()

    provider, client, _, _ = make_provider(handler)
    guard = FakeExecutionGuard(events)
    async with client:
        result = await provider.resume_upload(
            publishing_input(),
            YouTubeResumableUploadSession(SESSION_URI, 10),
            guard,
        )

    assert result.remote_media_id == VIDEO_ID
    assert events == ["renew-1", "probe", "renew-2", "media"]


@pytest.mark.parametrize(
    ("fail_on_call", "expected_events"),
    [
        (1, ["renew-1"]),
        (2, ["renew-1", "probe", "renew-2"]),
    ],
)
async def test_renewal_failure_prevents_following_remote_operation(
    fail_on_call: int,
    expected_events: list[str],
) -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("probe" if request.headers["content-range"] == "bytes */10" else "media")
        return httpx.Response(308, headers={"Range": "bytes=0-3"})

    provider, client, _, _ = make_provider(handler)
    guard = FakeExecutionGuard(events, fail_on_call=fail_on_call)
    async with client:
        with pytest.raises(PublishingExecutionLeaseLostError):
            await provider.resume_upload(
                publishing_input(),
                YouTubeResumableUploadSession(SESSION_URI, 10),
                guard,
            )

    assert events == expected_events


async def test_existing_session_already_complete_does_not_upload_again() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["content-range"] == "bytes */10"
        return completed_response()

    provider, client, _, _ = make_provider(handler)
    async with client:
        result = await provider.resume_upload(
            publishing_input(), YouTubeResumableUploadSession(SESSION_URI, 10)
        )
    assert len(requests) == 1
    assert result.remote_media_id == VIDEO_ID


async def test_status_probe_308_without_range_resumes_from_zero() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(308)
        assert request.headers["content-range"] == "bytes 0-9/10"
        assert request.content == VIDEO_BYTES
        return completed_response()

    provider, client, _, _ = make_provider(handler)
    async with client:
        result = await provider.resume_upload(
            publishing_input(), YouTubeResumableUploadSession(SESSION_URI, 10)
        )
    assert result.remote_media_id == VIDEO_ID


@pytest.mark.parametrize(
    "range_header",
    ["items=0-4", "bytes=4-5", "bytes=0-x", "bytes=0-10", "bytes=0--1"],
)
async def test_malformed_or_invalid_range_is_rejected_safely(range_header: str) -> None:
    provider, client, _, _ = make_provider(
        lambda _: httpx.Response(308, headers={"Range": range_header})
    )
    async with client:
        with pytest.raises(YouTubePublishingError) as error:
            await provider.resume_upload(
                publishing_input(), YouTubeResumableUploadSession(SESSION_URI, 10)
            )
    assert SESSION_URI not in repr(error.value)


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (httpx.Response(200), YouTubePublishingError),
        (httpx.Response(500, text=SESSION_URI), PublishingTransientError),
    ],
)
async def test_initiation_missing_location_or_http_failure_is_safe(
    response: httpx.Response,
    expected_error,
) -> None:
    provider, client, _, _ = make_provider(lambda _: response)
    async with client:
        with pytest.raises(expected_error) as error:
            await provider.publish(publishing_input())
    assert SESSION_URI not in repr(error.value)
    assert ACCESS_TOKEN not in repr(error.value)


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (httpx.Response(500, text=SESSION_URI), PublishingTransientError),
        (httpx.Response(200, content=b"{"), YouTubePublishingError),
        (httpx.Response(200, json={}), YouTubePublishingError),
    ],
)
async def test_upload_and_completion_failures_are_safe(
    response: httpx.Response, expected_error
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return initiation_response() if request.method == "POST" else response

    provider, client, _, _ = make_provider(handler)
    async with client:
        with pytest.raises(expected_error) as error:
            await provider.publish(publishing_input())
    assert str(error.value) in {
        "YouTube publishing failed",
        "Publishing provider is temporarily unavailable",
    }
    assert ACCESS_TOKEN not in repr(error.value)
    assert SESSION_URI not in repr(error.value)


@pytest.mark.parametrize("failure", ["timeout", "connection"])
async def test_transport_failures_are_normalized(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        raise httpx.ConnectError("secret", request=request)

    provider, client, _, _ = make_provider(handler)
    async with client:
        with pytest.raises(PublishingTransientError):
            await provider.publish(publishing_input())


async def test_invalid_existing_session_offset_is_rejected_before_http() -> None:
    provider, client, _, _ = make_provider(
        lambda _: (_ for _ in ()).throw(AssertionError("HTTP must not run"))
    )
    async with client:
        with pytest.raises(YouTubePublishingError):
            await provider.resume_upload(
                publishing_input(), YouTubeResumableUploadSession(SESSION_URI, 10, 10)
            )


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
