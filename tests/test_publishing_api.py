import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies as dependency_module
from app.api.dependencies import get_publishing_service
from app.api.v1.endpoints import publishing as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    PublishCancellationConflictError,
    PublishJobCancelledError,
    PublishJobNotFoundError,
    RenderNotReadyForPublishingError,
    UnusablePublishArtifactError,
    VideoRenderNotFoundError,
)
from app.models.publish_job import PublishStatus


def request_body(scheduled_publish_at=None) -> dict:
    return {
        "metadata": {
            "title": "Publish deliberately",
            "description": "A useful Short.",
            "tags": ["Editing", "Shorts"],
            "category": "education",
        },
        "options": {
            "platform": "youtube",
            "account_reference": "channel-main",
            "visibility": "private",
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": (
                scheduled_publish_at.isoformat() if scheduled_publish_at else None
            ),
        },
    }


def publish_job(state: PublishStatus, **values):
    now = datetime.now(UTC)
    defaults = {
        "id": 1,
        "video_render_id": 4,
        "status": state,
        "platform": "youtube",
        "scheduled_publish_at": None,
        "published_at": None,
        "completed_at": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def published_job(**values):
    now = datetime.now(UTC)
    defaults = {
        "account_reference": "channel-main",
        "title": "Publish deliberately",
        "description": "A useful Short.",
        "tags": ["Editing", "Shorts"],
        "category": "education",
        "visibility": "private",
        "made_for_kids": False,
        "notify_subscribers": False,
        "language": "en",
        "recording_date": None,
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
        "source_checksum": "sha256:abc",
        "source_file_size_bytes": 4096,
        "source_duration_seconds": 15,
        "remote_media_id": "local-youtube-abc",
        "remote_url": "https://publishing.local.invalid/youtube/local-youtube-abc",
        "remote_status": "published",
        "provider_metadata": {"synthetic": True},
        "published_at": now,
        "completed_at": now,
    }
    defaults.update(values)
    return publish_job(PublishStatus.PUBLISHED, **defaults)


class FakeService:
    def __init__(self, current=None, *, jobs=None, should_enqueue=True) -> None:
        self.current = current
        self.jobs = jobs or []
        self.should_enqueue = should_enqueue
        self.created = []
        self.enqueue_failures = []
        self.cancelled = []

    async def request_publish_job(self, render_id, request):
        self.created.append((render_id, request))
        if self.current is None:
            self.current = publish_job(
                PublishStatus.PENDING,
                id=len(self.jobs) + 1,
                video_render_id=render_id,
                scheduled_publish_at=request.options.scheduled_publish_at,
            )
            self.jobs.insert(0, self.current)
        return self.current, self.should_enqueue

    async def list_publish_jobs_for_render(self, render_id):
        return self.jobs

    async def get_publish_job(self, publish_job_id):
        return self.current

    async def prepare_publish_retry(self, publish_job_id):
        if self.current.status == PublishStatus.CANCELLED:
            raise PublishJobCancelledError
        if self.current.status == PublishStatus.FAILED:
            self.current.status = PublishStatus.PENDING
            self.current.completed_at = None
            self.current.error_message = None
        return self.current, self.should_enqueue

    async def mark_publish_enqueue_failed(self, current, error):
        current.status = PublishStatus.FAILED
        current.completed_at = None
        current.error_message = f"Publishing task enqueue failed: {error}"
        self.enqueue_failures.append((current, error))

    async def cancel_publish_job_and_commit(self, publish_job_id):
        if self.current.status in {PublishStatus.PUBLISHING, PublishStatus.PUBLISHED}:
            raise PublishCancellationConflictError
        self.current.status = PublishStatus.CANCELLED
        self.current.completed_at = datetime.now(UTC)
        self.current.error_message = None
        self.cancelled.append(publish_job_id)
        return self.current


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_publishing_service] = lambda: service
    return TestClient(app)


@pytest.mark.parametrize(
    "scheduled",
    [None, datetime.now(UTC) - timedelta(minutes=1)],
)
def test_create_immediate_or_due_job_queues_and_returns_202(monkeypatch, scheduled) -> None:
    current = publish_job(PublishStatus.PENDING, id=12, scheduled_publish_at=scheduled)
    service = FakeService(current, should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/renders/4/publish-jobs", json=request_body(scheduled))

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert queued == [12]
    assert service.created[0][0] == 4


def test_create_future_job_is_committed_pending_without_enqueue(monkeypatch) -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    current = publish_job(PublishStatus.PENDING, scheduled_publish_at=future)
    service = FakeService(current, should_enqueue=False)
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/renders/4/publish-jobs", json=request_body(future))

    assert response.status_code == 202
    assert response.json()["scheduled_publish_at"] == future.isoformat().replace("+00:00", "Z")
    assert queued == []


def test_multiple_creates_make_distinct_variants(monkeypatch) -> None:
    service = FakeService()
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)

    with client_for(service) as client:
        first = client.post("/api/v1/renders/4/publish-jobs", json=request_body())
        service.current = None
        second = client.post("/api/v1/renders/4/publish-jobs", json=request_body())

    assert first.json()["id"] != second.json()["id"]
    assert queued == [1, 2]
    assert len(service.jobs) == 2


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (VideoRenderNotFoundError(), 404),
        (RenderNotReadyForPublishingError(), 409),
        (UnusablePublishArtifactError(), 422),
    ],
)
def test_create_maps_service_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_publish_job(self, render_id, request):
            raise error

    with client_for(FailingService()) as client:
        response = client.post("/api/v1/renders/4/publish-jobs", json=request_body())

    assert response.status_code == expected_status


def test_invalid_request_returns_422() -> None:
    body = request_body()
    body["metadata"]["title"] = "   "
    with client_for(FakeService()) as client:
        response = client.post("/api/v1/renders/4/publish-jobs", json=body)
    assert response.status_code == 422


def test_create_broker_failure_returns_503_and_persists_same_failed_job(monkeypatch) -> None:
    current = publish_job(PublishStatus.PENDING)
    service = FakeService(current)

    def fail(publish_job_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.execute_publish, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/renders/4/publish-jobs", json=request_body())

    assert response.status_code == 503
    assert response.json() == {"detail": "Publishing could not be queued"}
    assert current.status == PublishStatus.FAILED
    assert current.error_message == "Publishing task enqueue failed: broker unavailable"


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (PublishStatus.PENDING, None),
        (PublishStatus.PUBLISHING, None),
        (PublishStatus.FAILED, "Upload failed"),
        (PublishStatus.CANCELLED, None),
    ],
)
def test_get_nonpublished_job_returns_status(state, message) -> None:
    current = publish_job(state, error_message=message)
    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/publish-jobs/1")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert response.json()["error_message"] == message
    assert "remote_media_id" not in response.json()


def test_get_published_job_returns_full_response() -> None:
    with client_for(FakeService(published_job())) as client:
        response = client.get("/api/v1/publish-jobs/1")

    assert response.status_code == 200
    assert response.json()["remote_media_id"] == "local-youtube-abc"
    assert response.json()["source_storage_key"] == "renders/4/output.mp4"


def test_get_missing_job_returns_404() -> None:
    class MissingService(FakeService):
        async def get_publish_job(self, publish_job_id):
            raise PublishJobNotFoundError

    with client_for(MissingService()) as client:
        assert client.get("/api/v1/publish-jobs/999").status_code == 404


def test_list_mixed_variants_newest_first_and_empty() -> None:
    newest = publish_job(PublishStatus.PENDING, id=2)
    oldest = published_job(id=1)
    with client_for(FakeService(jobs=[newest, oldest])) as client:
        mixed = client.get("/api/v1/renders/4/publish-jobs")
    assert [item["id"] for item in mixed.json()] == [2, 1]
    assert "remote_media_id" not in mixed.json()[0]
    assert mixed.json()[1]["remote_media_id"] == "local-youtube-abc"

    with client_for(FakeService(jobs=[])) as client:
        assert client.get("/api/v1/renders/4/publish-jobs").json() == []


@pytest.mark.parametrize("state", [PublishStatus.PENDING, PublishStatus.FAILED])
@pytest.mark.parametrize("future", [False, True])
def test_retry_reuses_row_preserves_intent_and_respects_schedule(
    monkeypatch, state, future
) -> None:
    scheduled = datetime.now(UTC) + timedelta(hours=1) if future else None
    current = publish_job(state, id=9, scheduled_publish_at=scheduled, error_message="Old")
    current.source_storage_key = "renders/4/output.mp4"
    current.source_checksum = "sha256:original"
    current.source_file_size_bytes = 4096
    current.source_duration_seconds = 15
    current.title = "Original title"
    current.tags = ["Editing"]
    current.publish_options = {"visibility": "private"}
    service = FakeService(current, jobs=[current], should_enqueue=not future)
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/publish-jobs/9/retry")

    assert response.status_code == 202
    assert current.status == PublishStatus.PENDING
    assert current.error_message is None if state == PublishStatus.FAILED else "Old"
    assert queued == ([] if future else [9])
    assert len(service.jobs) == 1
    assert (
        current.source_storage_key,
        current.source_checksum,
        current.source_file_size_bytes,
        current.source_duration_seconds,
        current.title,
        current.tags,
        current.publish_options,
    ) == (
        "renders/4/output.mp4",
        "sha256:original",
        4096,
        15,
        "Original title",
        ["Editing"],
        {"visibility": "private"},
    )


@pytest.mark.parametrize("state", [PublishStatus.PUBLISHING, PublishStatus.PUBLISHED])
def test_retry_active_or_published_returns_200_without_enqueue(monkeypatch, state) -> None:
    current = published_job() if state == PublishStatus.PUBLISHED else publish_job(state)
    service = FakeService(current, should_enqueue=False)
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)
    with client_for(service) as client:
        response = client.post("/api/v1/publish-jobs/1/retry")
    assert response.status_code == 200
    assert queued == []


def test_retry_cancelled_returns_409() -> None:
    with client_for(FakeService(publish_job(PublishStatus.CANCELLED))) as client:
        response = client.post("/api/v1/publish-jobs/1/retry")
    assert response.status_code == 409


def test_retry_broker_failure_returns_503(monkeypatch) -> None:
    current = publish_job(PublishStatus.FAILED)
    service = FakeService(current)

    def fail(publish_job_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.execute_publish, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/publish-jobs/1/retry")
    assert response.status_code == 503
    assert current.status == PublishStatus.FAILED


@pytest.mark.parametrize("state", [PublishStatus.PENDING, PublishStatus.FAILED])
def test_cancel_pending_or_failed_commits_without_enqueue(monkeypatch, state) -> None:
    current = publish_job(state)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.execute_publish, "delay", queued.append)
    with client_for(service) as client:
        response = client.post("/api/v1/publish-jobs/1/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert queued == []


def test_cancelled_is_idempotent() -> None:
    current = publish_job(PublishStatus.CANCELLED, completed_at=datetime.now(UTC))
    service = FakeService(current)
    with client_for(service) as client:
        first = client.post("/api/v1/publish-jobs/1/cancel")
        second = client.post("/api/v1/publish-jobs/1/cancel")
    assert first.status_code == second.status_code == 200


@pytest.mark.parametrize("state", [PublishStatus.PUBLISHING, PublishStatus.PUBLISHED])
def test_cancel_active_or_published_returns_409(state) -> None:
    current = published_job() if state == PublishStatus.PUBLISHED else publish_job(state)
    with client_for(FakeService(current)) as client:
        response = client.post("/api/v1/publish-jobs/1/cancel")
    assert response.status_code == 409


def test_dependency_composes_repositories_and_shared_provider(monkeypatch) -> None:
    session = object()
    render_repository = object()
    job_repository = object()
    provider = object()
    monkeypatch.setattr(
        dependency_module, "VideoRenderRepository", lambda received: render_repository
    )
    monkeypatch.setattr(dependency_module, "PublishJobRepository", lambda received: job_repository)
    factory = Mock(return_value=provider)
    monkeypatch.setattr(dependency_module, "create_publishing_provider", factory)

    service = dependency_module.get_publishing_service(session)

    assert service.video_render_repository is render_repository
    assert service.publish_job_repository is job_repository
    assert service.publishing_provider is provider
    factory.assert_called_once_with()


def test_routes_contain_no_repository_or_provider_construction() -> None:
    source = inspect.getsource(endpoint_module)

    assert "Repository(" not in source
    assert "LocalPublishingProvider(" not in source
    assert "process_publish_job(" not in source
    assert "source_storage_key" not in source
    assert "remote_media_id =" not in source
