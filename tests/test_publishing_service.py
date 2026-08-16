from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.exceptions import (
    PublishCancellationConflictError,
    PublishingError,
    PublishJobCancelledError,
    PublishJobNotFoundError,
    PublishNotDueError,
    RenderNotReadyForPublishingError,
    UnusablePublishArtifactError,
    VideoRenderNotFoundError,
)
from app.models.publish_job import PublishJob, PublishStatus
from app.models.video_render import VideoRender, VideoRenderStatus
from app.schemas.publish_job import PublishingInput, PublishRequest
from app.services.publishing_service import PublishingService


class FakeRepository:
    def __init__(self, rows=None) -> None:
        self.rows = {row.id: row for row in rows or []}
        self.created = []
        self.saved_statuses = []

    async def create(self, row):
        row.id = max(self.rows, default=0) + 1
        self.rows[row.id] = row
        self.created.append(row)
        return row

    async def get(self, row_id: int):
        return self.rows.get(row_id)

    async def get_by_video_render_id(self, video_render_id: int):
        return [row for row in self.rows.values() if row.video_render_id == video_render_id]

    async def save(self, row):
        self.rows[row.id] = row
        self.saved_statuses.append(row.status)
        return row


class RecordingProvider:
    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.inputs: list[PublishingInput] = []

    async def publish(self, publishing_input: PublishingInput):
        self.calls += 1
        self.inputs.append(publishing_input)
        if self.error is not None:
            raise self.error
        return self.result or {
            "remote_media_id": "youtube-123",
            "remote_url": "https://www.youtube.com/watch?v=youtube-123",
            "remote_status": "processed",
            "published_at": datetime.now(UTC),
            "provider_metadata": {"privacyStatus": "private"},
        }


def completed_render(**values) -> VideoRender:
    defaults = {
        "id": 4,
        "script_id": 2,
        "voice_track_id": 3,
        "status": VideoRenderStatus.COMPLETED,
        "storage_key": "renders/4/output.mp4",
        "checksum": "sha256:original",
        "file_size_bytes": 4096,
        "duration_seconds": 15,
    }
    defaults.update(values)
    return VideoRender(**defaults)


def publish_request(**option_changes) -> PublishRequest:
    options = {
        "platform": "youtube",
        "account_reference": " channel-main ",
        "visibility": "private",
        "made_for_kids": False,
        "notify_subscribers": False,
        "language": "en",
    }
    options.update(option_changes)
    return PublishRequest(
        metadata={
            "title": "  Publish deliberately  ",
            "description": "A useful Short.",
            "tags": [" Editing ", "Shorts", "editing"],
            "category": " education ",
        },
        options=options,
    )


def existing_job(status: PublishStatus = PublishStatus.PENDING, **values) -> PublishJob:
    defaults = {
        "id": 1,
        "video_render_id": 4,
        "status": status,
        "platform": "youtube",
        "account_reference": "channel-main",
        "title": "Publish deliberately",
        "description": "A useful Short.",
        "tags": ["Editing", "Shorts"],
        "category": "education",
        "visibility": "private",
        "made_for_kids": False,
        "notify_subscribers": False,
        "language": "en",
        "publish_options": {
            "platform": "youtube",
            "account_reference": "channel-main",
            "visibility": "private",
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": None,
        },
        "source_storage_key": "renders/4/output.mp4",
        "source_checksum": "sha256:original",
        "source_file_size_bytes": 4096,
        "source_duration_seconds": 15,
        "provider_metadata": {},
    }
    defaults.update(values)
    return PublishJob(**defaults)


def make_service(*, render=None, jobs=None, provider=None):
    render_repository = FakeRepository([render] if render is not None else [])
    job_repository = FakeRepository(jobs)
    provider = provider or RecordingProvider()
    service = PublishingService(render_repository, job_repository, provider)
    return service, job_repository, provider


async def test_create_pending_job_normalizes_and_snapshots_without_provider_call() -> None:
    render = completed_render()
    service, jobs, provider = make_service(render=render)

    job = await service.create_publish_job(4, publish_request())

    assert job.status == PublishStatus.PENDING
    assert job.account_reference == "channel-main"
    assert job.title == "Publish deliberately"
    assert job.tags == ["Editing", "Shorts"]
    assert job.category == "education"
    assert job.publish_options["visibility"] == "private"
    assert job.source_storage_key == "renders/4/output.mp4"
    assert job.source_checksum == "sha256:original"
    assert job.source_file_size_bytes == 4096
    assert job.source_duration_seconds == 15
    assert jobs.created == [job]
    assert provider.calls == 0


async def test_create_allows_multiple_variants() -> None:
    service, jobs, _ = make_service(render=completed_render())

    first = await service.create_publish_job(4, publish_request(visibility="private"))
    second = await service.create_publish_job(4, publish_request(visibility="unlisted"))

    assert first.id != second.id
    assert first.video_render_id == second.video_render_id == 4
    assert first.publish_options["visibility"] == "private"
    assert second.publish_options["visibility"] == "unlisted"
    assert len(jobs.rows) == 2


async def test_create_missing_render() -> None:
    service, _, _ = make_service()
    with pytest.raises(VideoRenderNotFoundError):
        await service.create_publish_job(4, publish_request())


async def test_create_rejects_incomplete_render() -> None:
    service, _, _ = make_service(render=completed_render(status=VideoRenderStatus.RENDERING))
    with pytest.raises(RenderNotReadyForPublishingError):
        await service.create_publish_job(4, publish_request())


@pytest.mark.parametrize(
    "changes",
    [
        {"storage_key": None},
        {"storage_key": "   "},
        {"duration_seconds": None},
        {"duration_seconds": 0},
        {"file_size_bytes": None},
        {"file_size_bytes": -1},
        {"checksum": "   "},
    ],
)
async def test_create_rejects_unusable_artifact(changes: dict) -> None:
    service, _, _ = make_service(render=completed_render(**changes))
    with pytest.raises(UnusablePublishArtifactError):
        await service.create_publish_job(4, publish_request())


async def test_process_maps_result_and_crosses_provider_boundary_with_schema_only() -> None:
    job = existing_job()
    service, jobs, provider = make_service(render=completed_render(), jobs=[job])

    result = await service.process_publish_job(1)

    assert jobs.saved_statuses == [PublishStatus.PUBLISHING, PublishStatus.PUBLISHED]
    assert result.status == PublishStatus.PUBLISHED
    assert result.remote_media_id == "youtube-123"
    assert result.remote_url == "https://www.youtube.com/watch?v=youtube-123"
    assert result.remote_status == "processed"
    assert result.provider_metadata == {"privacyStatus": "private"}
    assert result.published_at is not None
    assert result.completed_at is not None
    assert provider.calls == 1
    boundary = provider.inputs[0]
    assert isinstance(boundary, PublishingInput)
    assert boundary.platform.value == "youtube"
    assert boundary.account_reference == "channel-main"
    assert boundary.source_checksum == "sha256:original"
    assert not hasattr(boundary, "video_render")
    assert not hasattr(boundary, "session")
    assert not hasattr(boundary, "oauth_token")
    assert not hasattr(boundary, "refresh_token")


async def test_published_job_is_idempotent() -> None:
    job = existing_job(
        PublishStatus.PUBLISHED,
        remote_media_id="existing-id",
        remote_url="https://example.com/existing",
        published_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    provider = RecordingProvider(error=AssertionError("provider must not run"))
    service, jobs, _ = make_service(jobs=[job], provider=provider)

    result = await service.process_publish_job(1)

    assert result is job
    assert result.remote_media_id == "existing-id"
    assert provider.calls == 0
    assert jobs.saved_statuses == []


async def test_failed_job_retries_same_row_and_uses_persisted_snapshot() -> None:
    render = completed_render(
        storage_key="renders/4/replaced.mp4",
        checksum="sha256:changed",
        file_size_bytes=9999,
        duration_seconds=99,
    )
    job = existing_job(PublishStatus.FAILED, error_message="Old failure")
    service, jobs, provider = make_service(render=render, jobs=[job])

    result = await service.process_publish_job(1)

    assert result is job
    assert len(jobs.rows) == 1
    assert provider.inputs[0].source_storage_key == "renders/4/output.mp4"
    assert provider.inputs[0].source_checksum == "sha256:original"
    assert provider.inputs[0].source_file_size_bytes == 4096
    assert provider.inputs[0].source_duration_seconds == 15


@pytest.mark.parametrize(
    ("provider", "expected_message"),
    [
        (RecordingProvider(error=RuntimeError("upload failed")), "upload failed"),
        (
            RecordingProvider(result={"remote_media_id": "bad"}),
            "Publishing provider returned an invalid structured result",
        ),
    ],
)
async def test_provider_failure_persists_failed_state(provider, expected_message: str) -> None:
    job = existing_job()
    service, jobs, _ = make_service(jobs=[job], provider=provider)

    with pytest.raises(PublishingError) as caught:
        await service.process_publish_job(1)

    assert caught.value.__cause__ is not None
    assert job.status == PublishStatus.FAILED
    assert job.completed_at is None
    assert job.error_message == expected_message
    assert jobs.saved_statuses == [PublishStatus.PUBLISHING, PublishStatus.FAILED]


async def test_malformed_persisted_input_is_failed_without_provider_call() -> None:
    job = existing_job(publish_options={"account_reference": "   "})
    service, jobs, provider = make_service(jobs=[job])

    with pytest.raises(PublishingError):
        await service.process_publish_job(1)

    assert provider.calls == 0
    assert job.status == PublishStatus.FAILED
    assert job.error_message == "Persisted publish job contains invalid structured input"
    assert jobs.saved_statuses == [PublishStatus.PUBLISHING, PublishStatus.FAILED]


async def test_future_schedule_is_not_executed_or_transitioned() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    job = existing_job(
        scheduled_publish_at=future,
        publish_options=publish_request(scheduled_publish_at=future).options.model_dump(
            mode="json"
        ),
    )
    service, jobs, provider = make_service(jobs=[job])

    with pytest.raises(PublishNotDueError):
        await service.process_publish_job(1)

    assert job.status == PublishStatus.PENDING
    assert jobs.saved_statuses == []
    assert provider.calls == 0


async def test_due_timezone_aware_schedule_can_publish() -> None:
    due = datetime.now(UTC) - timedelta(seconds=1)
    job = existing_job(
        scheduled_publish_at=due,
        publish_options=publish_request(scheduled_publish_at=due).options.model_dump(mode="json"),
    )
    service, _, provider = make_service(jobs=[job])

    result = await service.process_publish_job(1)

    assert result.status == PublishStatus.PUBLISHED
    assert provider.calls == 1
    assert provider.inputs[0].scheduled_publish_at == due


async def test_naive_persisted_schedule_is_not_reinterpreted() -> None:
    job = existing_job(scheduled_publish_at=datetime(2026, 8, 14, 10))
    service, jobs, provider = make_service(jobs=[job])

    with pytest.raises(PublishNotDueError):
        await service.process_publish_job(1)

    assert jobs.saved_statuses == []
    assert provider.calls == 0


@pytest.mark.parametrize("state", [PublishStatus.PENDING, PublishStatus.FAILED])
async def test_pending_and_failed_jobs_can_be_cancelled(state) -> None:
    job = existing_job(state, error_message="Old failure")
    service, jobs, provider = make_service(jobs=[job])

    result = await service.cancel_publish_job(1)

    assert result.status == PublishStatus.CANCELLED
    assert result.completed_at is not None
    assert result.error_message is None
    assert jobs.saved_statuses == [PublishStatus.CANCELLED]
    assert provider.calls == 0


async def test_cancelled_job_is_idempotent_and_never_processed() -> None:
    job = existing_job(PublishStatus.CANCELLED, completed_at=datetime.now(UTC))
    service, jobs, provider = make_service(jobs=[job])

    assert await service.cancel_publish_job(1) is job
    with pytest.raises(PublishJobCancelledError):
        await service.process_publish_job(1)

    assert jobs.saved_statuses == []
    assert provider.calls == 0


@pytest.mark.parametrize("state", [PublishStatus.PUBLISHING, PublishStatus.PUBLISHED])
async def test_active_or_published_job_cancellation_conflicts(state) -> None:
    job = existing_job(state)
    service, jobs, _ = make_service(jobs=[job])

    with pytest.raises(PublishCancellationConflictError):
        await service.cancel_publish_job(1)

    assert job.status == state
    assert jobs.saved_statuses == []


async def test_get_and_list_delegate_to_repositories() -> None:
    first = existing_job(id=1)
    second = existing_job(id=2)
    service, _, _ = make_service(render=completed_render(), jobs=[second, first])

    assert await service.get_publish_job(1) is first
    assert await service.list_publish_jobs_for_render(4) == [second, first]
    with pytest.raises(PublishJobNotFoundError):
        await service.get_publish_job(999)
    with pytest.raises(VideoRenderNotFoundError):
        await service.list_publish_jobs_for_render(999)


async def test_request_publish_job_creates_and_reports_due_state_without_transaction() -> None:
    service, jobs, _ = make_service(render=completed_render())

    job, should_enqueue = await service.request_publish_job(4, publish_request())

    assert job.status == PublishStatus.PENDING
    assert should_enqueue is True
    assert jobs.created == [job]


async def test_request_future_job_creates_without_being_due() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    service, jobs, _ = make_service(render=completed_render())

    job, should_enqueue = await service.request_publish_job(
        4, publish_request(scheduled_publish_at=future)
    )

    assert job.scheduled_publish_at == future
    assert should_enqueue is False
    assert jobs.created == [job]


async def test_prepare_failed_retry_preserves_intent_without_transaction() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    job = existing_job(
        PublishStatus.FAILED,
        error_message="Old failure",
        scheduled_publish_at=future,
        publish_options=publish_request(scheduled_publish_at=future).options.model_dump(
            mode="json"
        ),
    )
    original = (
        job.source_storage_key,
        job.source_checksum,
        job.source_file_size_bytes,
        job.source_duration_seconds,
        list(job.tags),
        dict(job.publish_options),
    )
    service, jobs, _ = make_service(jobs=[job])

    result, should_enqueue = await service.prepare_publish_retry(1)

    assert result is job
    assert result.status == PublishStatus.PENDING
    assert result.error_message is None
    assert should_enqueue is False
    assert jobs.saved_statuses == [PublishStatus.PENDING]
    assert original == (
        job.source_storage_key,
        job.source_checksum,
        job.source_file_size_bytes,
        job.source_duration_seconds,
        job.tags,
        job.publish_options,
    )


async def test_mark_enqueue_failed_and_cancel_mutate_without_transaction() -> None:
    first = existing_job()
    service, jobs, _ = make_service(jobs=[first])

    await service.mark_publish_enqueue_failed(first, RuntimeError("broker unavailable"))

    assert first.status == PublishStatus.FAILED
    assert first.error_message == "Publishing task enqueue failed: broker unavailable"
    assert jobs.saved_statuses == [PublishStatus.FAILED]

    await service.cancel_publish_job(1)
    assert first.status == PublishStatus.CANCELLED
    assert jobs.saved_statuses == [PublishStatus.FAILED, PublishStatus.CANCELLED]
