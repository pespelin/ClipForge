import inspect
import json
import socket
from datetime import UTC, datetime, timedelta, timezone
from typing import get_type_hints

import pytest

from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing import (
    LocalPublishingProvider,
    PublishingProvider,
    UnsupportedPublishingPlatformError,
    UnusablePublishingInputError,
    create_publishing_provider,
)
from app.schemas.publish_job import PublishingInput, PublishingResult


def publishing_input(**values) -> PublishingInput:
    defaults = {
        "publish_job_id": 5,
        "video_render_id": 4,
        "platform": "youtube",
        "account_reference": "channel-main",
        "source_storage_key": "renders/4/output.mp4",
        "source_checksum": "sha256:abc",
        "source_file_size_bytes": 4096,
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
            "account_reference": "channel-main",
            "visibility": "private",
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": None,
        },
    }
    defaults.update(values)
    return PublishingInput.model_validate(defaults)


def test_protocol_compatibility_and_async_boundary() -> None:
    provider: PublishingProvider = LocalPublishingProvider()

    assert isinstance(provider, LocalPublishingProvider)
    assert inspect.iscoroutinefunction(provider.publish)


def test_shared_factory_returns_local_provider_through_abstraction() -> None:
    provider = create_publishing_provider()

    assert isinstance(provider, LocalPublishingProvider)
    assert get_type_hints(create_publishing_provider)["return"] is PublishingProvider


async def test_identical_input_produces_identical_complete_result() -> None:
    provider = LocalPublishingProvider()
    provider_input = publishing_input()

    first = await provider.publish(provider_input)
    second = await provider.publish(provider_input)

    assert first == second
    assert PublishingResult.model_validate(first) == first
    assert first.remote_media_id.startswith("local-youtube-")
    assert first.remote_status == "published"
    assert first.published_at.tzinfo is not None
    assert first.published_at.utcoffset() is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"account_reference": "channel-secondary"},
        {"source_checksum": "sha256:different"},
        {"source_storage_key": "renders/4/alternate.mp4"},
        {"title": "A different title"},
        {"visibility": "unlisted"},
    ],
)
async def test_identity_changes_with_stable_identity_fields(changes: dict) -> None:
    provider = LocalPublishingProvider()

    original = await provider.publish(publishing_input())
    changed = await provider.publish(publishing_input(**changes))

    assert changed.remote_media_id != original.remote_media_id
    assert changed.remote_url != original.remote_url


@pytest.mark.parametrize(
    "visibility",
    [PublishVisibility.PUBLIC, PublishVisibility.UNLISTED, PublishVisibility.PRIVATE],
)
async def test_all_visibility_values_are_preserved(visibility) -> None:
    result = await LocalPublishingProvider().publish(publishing_input(visibility=visibility))

    assert result.provider_metadata["visibility"] == visibility.value


async def test_synthetic_url_and_metadata_are_explicit_and_json_safe() -> None:
    provider_input = publishing_input()
    result = await LocalPublishingProvider().publish(provider_input)
    url = str(result.remote_url)

    assert url.startswith("https://publishing.local.invalid/youtube/")
    assert "youtube.com" not in url
    assert result.provider_metadata["synthetic"] is True
    assert result.provider_metadata["provider"] == "local"
    assert result.provider_metadata["simulated_platform"] == "youtube"
    assert result.provider_metadata["real_publication"] is False
    assert result.provider_metadata["tags"] == provider_input.tags
    assert json.loads(json.dumps(result.provider_metadata)) == result.provider_metadata
    assert (
        not {
            "oauth_token",
            "refresh_token",
            "api_key",
            "client_secret",
        }
        & result.provider_metadata.keys()
    )


async def test_unscheduled_published_at_is_stable_synthetic_utc_time() -> None:
    provider = LocalPublishingProvider()
    provider_input = publishing_input()

    first = await provider.publish(provider_input)
    second = await provider.publish(provider_input)

    assert first.published_at == second.published_at
    assert datetime(2000, 1, 1, tzinfo=UTC) <= first.published_at < datetime(2020, 1, 1, tzinfo=UTC)
    assert first.published_at.tzinfo is UTC


async def test_due_schedule_is_normalized_to_utc_and_used_as_published_at() -> None:
    scheduled = datetime.now(timezone(timedelta(hours=3))) - timedelta(minutes=1)
    options = publishing_input().publish_options.model_copy(
        update={"scheduled_publish_at": scheduled}
    )
    provider_input = publishing_input(
        scheduled_publish_at=scheduled,
        publish_options=options,
    )

    result = await LocalPublishingProvider().publish(provider_input)

    assert result.published_at == scheduled.astimezone(UTC)
    assert result.published_at.tzinfo is UTC
    assert result.provider_metadata["scheduled"] is True


async def test_future_schedule_is_defensively_rejected() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    unchecked = publishing_input().model_copy(update={"scheduled_publish_at": future})

    with pytest.raises(UnusablePublishingInputError, match="still in the future"):
        await LocalPublishingProvider().publish(unchecked)


async def test_unsupported_platform_is_explicitly_rejected() -> None:
    for platform in (
        PublishPlatform.TIKTOK,
        PublishPlatform.INSTAGRAM,
        PublishPlatform.OTHER,
    ):
        unchecked = publishing_input().model_copy(update={"platform": platform})
        with pytest.raises(UnsupportedPublishingPlatformError):
            await LocalPublishingProvider().publish(unchecked)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"account_reference": "   "}, "account_reference"),
        ({"source_storage_key": "   "}, "source_storage_key"),
        ({"source_checksum": "   "}, "source_checksum"),
        ({"source_file_size_bytes": -1}, "source_file_size_bytes"),
        ({"source_duration_seconds": 0}, "source_duration_seconds"),
        ({"title": "   "}, "title"),
        ({"scheduled_publish_at": datetime(2026, 8, 14, 10)}, "timezone"),
    ],
)
async def test_defensive_validation_rejects_unchecked_invalid_input(
    changes: dict, message: str
) -> None:
    unchecked = publishing_input().model_copy(update=changes)

    with pytest.raises(UnusablePublishingInputError, match=message):
        await LocalPublishingProvider().publish(unchecked)


async def test_provider_makes_no_network_call(monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    result = await LocalPublishingProvider().publish(publishing_input())

    assert result.remote_status == "published"


async def test_input_and_nested_values_are_not_mutated() -> None:
    provider_input = publishing_input()
    original = provider_input.model_dump(mode="json")

    await LocalPublishingProvider().publish(provider_input)

    assert provider_input.model_dump(mode="json") == original
