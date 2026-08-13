from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.publish_job import PublishPlatform, PublishStatus, PublishVisibility
from app.schemas.publish_job import (
    PublishJobResponse,
    PublishJobStatusResponse,
    PublishMetadata,
    PublishOptions,
    PublishRequest,
)


def options_data(**values) -> dict:
    result = {"account_reference": "channel-main"}
    result.update(values)
    return result


def response_data(**values) -> dict:
    now = datetime.now(UTC)
    options = PublishOptions(**options_data())
    defaults = {
        "id": 1,
        "video_render_id": 2,
        "status": "pending",
        "platform": "youtube",
        "account_reference": "channel-main",
        "title": "A useful Short",
        "description": "A concise description.",
        "tags": ["editing", "shorts"],
        "category": "education",
        "visibility": "private",
        "made_for_kids": False,
        "notify_subscribers": False,
        "language": "en",
        "recording_date": None,
        "scheduled_publish_at": None,
        "publish_options": options.model_dump(mode="json"),
        "source_storage_key": "renders/2/output.mp4",
        "source_checksum": "sha256:abc",
        "source_file_size_bytes": 4096,
        "source_duration_seconds": 12.5,
        "remote_media_id": None,
        "remote_url": None,
        "remote_status": None,
        "provider_metadata": {},
        "published_at": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return defaults


def test_options_use_safe_defaults_and_normalize_account() -> None:
    options = PublishOptions(account_reference="  channel-main  ")

    assert options.platform is PublishPlatform.YOUTUBE
    assert options.visibility is PublishVisibility.PRIVATE
    assert options.account_reference == "channel-main"
    assert options.made_for_kids is False
    assert options.notify_subscribers is False
    assert options.language == "en"
    assert options.scheduled_publish_at is None


@pytest.mark.parametrize(
    "changes",
    [
        {"account_reference": "   "},
        {"language": "english"},
        {"platform": "vimeo"},
        {"visibility": "friends"},
    ],
)
def test_options_reject_invalid_identifiers_and_enums(changes: dict) -> None:
    with pytest.raises(ValidationError):
        PublishOptions(**options_data(**changes))


def test_schedule_requires_explicit_timezone() -> None:
    aware = datetime(2026, 8, 14, 10, tzinfo=UTC)
    assert PublishOptions(**options_data(scheduled_publish_at=aware)).scheduled_publish_at == aware

    with pytest.raises(ValidationError):
        PublishOptions(**options_data(scheduled_publish_at=datetime(2026, 8, 14, 10)))


def test_metadata_normalizes_title_category_and_tags_stably() -> None:
    metadata = PublishMetadata(
        title="  Editing deliberately  ",
        category="  education  ",
        tags=[" Editing ", "Shorts", "editing", " SHORTS ", "Tips"],
    )

    assert metadata.title == "Editing deliberately"
    assert metadata.category == "education"
    assert metadata.tags == ["Editing", "Shorts", "Tips"]


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "   "},
        {"title": "x" * 201},
        {"description": "x" * 10_001},
        {"category": "   "},
        {"tags": ["editing", "   "]},
        {"tags": ["x" * 101]},
        {"tags": [str(index) for index in range(51)]},
    ],
)
def test_metadata_rejects_invalid_bounds_and_blank_values(changes: dict) -> None:
    values = {"title": "Valid title"}
    values.update(changes)
    with pytest.raises(ValidationError):
        PublishMetadata(**values)


def test_request_separates_metadata_from_options() -> None:
    request = PublishRequest(
        metadata={"title": "Publish this", "tags": ["shorts"]},
        options=options_data(),
    )

    assert request.metadata.title == "Publish this"
    assert request.options.visibility is PublishVisibility.PRIVATE


def test_status_and_complete_response_validate() -> None:
    status = PublishJobStatusResponse.model_validate(response_data())
    assert status.status is PublishStatus.PENDING

    response = PublishJobResponse.model_validate(response_data())
    assert response.source_storage_key == "renders/2/output.mp4"
    assert response.publish_options.visibility is PublishVisibility.PRIVATE


@pytest.mark.parametrize(
    "changes",
    [
        {"source_storage_key": "   "},
        {"source_checksum": "   "},
        {"source_file_size_bytes": -1},
        {"source_duration_seconds": 0},
    ],
)
def test_response_requires_usable_source_artifact_snapshot(changes: dict) -> None:
    with pytest.raises(ValidationError):
        PublishJobResponse.model_validate(response_data(**changes))


def test_published_response_requires_remote_id_and_terminal_times() -> None:
    now = datetime.now(UTC)
    published = PublishJobResponse.model_validate(
        response_data(
            status="published",
            remote_media_id="youtube-123",
            remote_url="https://www.youtube.com/watch?v=youtube-123",
            remote_status="processed",
            provider_metadata={"privacyStatus": "private"},
            published_at=now,
            completed_at=now,
        )
    )
    assert published.status is PublishStatus.PUBLISHED
    assert str(published.remote_url).startswith("https://www.youtube.com/")

    for changes in (
        {"remote_media_id": None, "published_at": now, "completed_at": now},
        {"remote_media_id": "youtube-123", "published_at": None, "completed_at": now},
        {"remote_media_id": "youtube-123", "published_at": now, "completed_at": None},
    ):
        with pytest.raises(ValidationError):
            PublishJobResponse.model_validate(response_data(status="published", **changes))


def test_response_rejects_naive_publication_datetimes_and_invalid_remote_url() -> None:
    with pytest.raises(ValidationError):
        PublishJobStatusResponse.model_validate(
            response_data(scheduled_publish_at=datetime(2026, 8, 14, 10))
        )
    with pytest.raises(ValidationError):
        PublishJobResponse.model_validate(
            response_data(scheduled_publish_at=datetime(2026, 8, 14, 10))
        )
    with pytest.raises(ValidationError):
        PublishJobResponse.model_validate(response_data(remote_url="not-a-url"))
