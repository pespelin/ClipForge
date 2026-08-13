import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.core.exceptions import (
    PublishingError,
    PublishJobCancelledError,
    PublishNotDueError,
)
from app.models.publish_job import PublishJob, PublishStatus
from app.providers.publishing import LocalPublishingProvider
from app.services.publishing_service import PublishingService
from app.tasks import publishing as task_module


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def patch_dependencies(monkeypatch, session: FakeSession, service_type: type) -> None:
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRenderRepository", lambda received: object())
    monkeypatch.setattr(task_module, "PublishJobRepository", lambda received: object())
    monkeypatch.setattr(task_module, "create_publishing_provider", object)
    monkeypatch.setattr(task_module, "PublishingService", service_type)


async def test_task_composes_dependencies_and_returns_result(monkeypatch) -> None:
    session = FakeSession()
    dependencies = {}
    render_repository = object()
    publish_job_repository = object()
    provider = object()

    class FakeService:
        def __init__(self, **values) -> None:
            dependencies.update(values)

        async def process_publish_job(self, publish_job_id: int):
            dependencies["publish_job_id"] = publish_job_id
            return SimpleNamespace(
                id=publish_job_id,
                status=PublishStatus.PUBLISHED,
                remote_media_id="local-youtube-abc",
            )

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRenderRepository", lambda received: render_repository)
    monkeypatch.setattr(
        task_module, "PublishJobRepository", lambda received: publish_job_repository
    )
    factory = Mock(return_value=provider)
    monkeypatch.setattr(task_module, "create_publishing_provider", factory)
    monkeypatch.setattr(task_module, "PublishingService", FakeService)

    result = await task_module._run_publishing(7)

    assert result == {
        "publish_job_id": 7,
        "publish_status": "published",
        "remote_media_id": "local-youtube-abc",
    }
    assert dependencies["video_render_repository"] is render_repository
    assert dependencies["publish_job_repository"] is publish_job_repository
    assert dependencies["publishing_provider"] is provider
    factory.assert_called_once_with()
    assert dependencies["publish_job_id"] == 7
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_sync_entrypoint_runs_async_helper_and_is_registered(monkeypatch) -> None:
    async def fake_run(publish_job_id: int) -> dict[str, int | str | None]:
        return {
            "publish_job_id": publish_job_id,
            "publish_status": "published",
            "remote_media_id": "local-youtube-abc",
        }

    monkeypatch.setattr(task_module, "_run_publishing", fake_run)

    assert task_module.execute_publish.run(7)["remote_media_id"] == "local-youtube-abc"
    assert task_module.execute_publish.name == "publish.execute"
    assert task_module.celery_app.tasks["publish.execute"].name == "publish.execute"
    assert "app.tasks.publishing" in task_module.celery_app.conf.include


async def test_publishing_error_commits_failed_state_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_publish_job(self, publish_job_id: int):
            raise PublishingError

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(PublishingError):
        await task_module._run_publishing(7)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


@pytest.mark.parametrize("error", [PublishNotDueError(), PublishJobCancelledError()])
async def test_precondition_errors_roll_back_and_reraise(monkeypatch, error) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_publish_job(self, publish_job_id: int):
            raise error

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(type(error)):
        await task_module._run_publishing(7)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


class InMemoryPublishJobRepository:
    def __init__(self, row: PublishJob) -> None:
        self.row = row
        self.saved_statuses = []

    async def get(self, publish_job_id: int):
        return self.row if self.row.id == publish_job_id else None

    async def save(self, row):
        self.row = row
        self.saved_statuses.append(row.status)
        return row


class UnexpectedRenderRepository:
    async def get(self, video_render_id: int):
        raise AssertionError("processing must use the persisted source snapshot")


class CountingProvider(LocalPublishingProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, publishing_input):
        self.calls += 1
        return await super().publish(publishing_input)


def publish_job(status: PublishStatus, **values) -> PublishJob:
    defaults = {
        "id": 7,
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


def patch_real_service(
    monkeypatch,
    session: FakeSession,
    repository: InMemoryPublishJobRepository,
    provider: CountingProvider,
) -> None:
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        task_module, "VideoRenderRepository", lambda received: UnexpectedRenderRepository()
    )
    monkeypatch.setattr(task_module, "PublishJobRepository", lambda received: repository)
    monkeypatch.setattr(task_module, "create_publishing_provider", lambda: provider)
    monkeypatch.setattr(task_module, "PublishingService", PublishingService)


async def test_published_job_is_idempotent_and_preserves_remote_metadata(monkeypatch) -> None:
    session = FakeSession()
    published_at = datetime(2010, 1, 1, tzinfo=UTC)
    metadata = {"synthetic": True, "identity_hash": "existing"}
    job = publish_job(
        PublishStatus.PUBLISHED,
        remote_media_id="local-youtube-existing",
        remote_url="https://publishing.local.invalid/youtube/local-youtube-existing",
        remote_status="published",
        provider_metadata=metadata.copy(),
        published_at=published_at,
        completed_at=datetime.now(UTC),
    )
    repository = InMemoryPublishJobRepository(job)
    provider = CountingProvider()
    patch_real_service(monkeypatch, session, repository, provider)

    result = await task_module._run_publishing(7)

    assert result["remote_media_id"] == "local-youtube-existing"
    assert provider.calls == 0
    assert job.remote_url.endswith("local-youtube-existing")
    assert job.published_at == published_at
    assert job.provider_metadata == metadata
    assert repository.saved_statuses == []
    assert session.commits == 1
    assert session.closed


async def test_failed_job_retries_same_row_and_preserves_source_snapshot(monkeypatch) -> None:
    session = FakeSession()
    job = publish_job(PublishStatus.FAILED, error_message="Previous failure")
    repository = InMemoryPublishJobRepository(job)
    provider = CountingProvider()
    patch_real_service(monkeypatch, session, repository, provider)

    result = await task_module._run_publishing(7)

    assert result["publish_job_id"] == 7
    assert result["publish_status"] == "published"
    assert repository.row is job
    assert provider.calls == 1
    assert job.source_storage_key == "renders/4/output.mp4"
    assert job.source_checksum == "sha256:original"
    assert job.source_file_size_bytes == 4096
    assert job.source_duration_seconds == 15
    assert repository.saved_statuses == [PublishStatus.PUBLISHING, PublishStatus.PUBLISHED]
    assert session.commits == 1


@pytest.mark.parametrize(
    ("state", "scheduled", "expected_error"),
    [
        (PublishStatus.CANCELLED, None, PublishJobCancelledError),
        (
            PublishStatus.PENDING,
            datetime.now(UTC) + timedelta(hours=1),
            PublishNotDueError,
        ),
    ],
)
async def test_cancelled_and_not_due_jobs_never_call_provider(
    monkeypatch, state, scheduled, expected_error
) -> None:
    session = FakeSession()
    job = publish_job(state, scheduled_publish_at=scheduled)
    repository = InMemoryPublishJobRepository(job)
    provider = CountingProvider()
    patch_real_service(monkeypatch, session, repository, provider)

    with pytest.raises(expected_error):
        await task_module._run_publishing(7)

    assert provider.calls == 0
    assert repository.saved_statuses == []
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


def test_operational_error_uses_bounded_celery_retry(monkeypatch) -> None:
    async def fail_with_operational_error(publish_job_id: int):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(task_module, "_run_publishing", fail_with_operational_error)
    monkeypatch.setattr(task_module.execute_publish, "retry", retry)

    with pytest.raises(Retry):
        task_module.execute_publish.run(7)

    retry.assert_called_once()
    assert task_module.execute_publish.autoretry_for == (OperationalError,)
    assert task_module.execute_publish.retry_backoff is True
    assert task_module.execute_publish.max_retries == 3


def test_task_contains_only_composition_and_transaction_boundary() -> None:
    source = inspect.getsource(task_module._run_publishing)

    assert "process_publish_job" in source
    assert "hashlib" not in source
    assert "scheduled_publish_at" not in source
    assert "source_storage_key" not in source
    assert "source_checksum" not in source
    assert "remote_media_id =" not in source
    assert "cancel" not in source
