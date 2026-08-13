import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_publishing_service
from app.api.v1.endpoints import publishing as publishing_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import PublishingError
from app.models.publish_job import PublishJob, PublishStatus
from app.models.video_render import (
    RenderAudioCodec,
    RenderFitMode,
    RenderOutputFormat,
    ResolutionPreset,
    VideoCodec,
    VideoRender,
    VideoRenderStatus,
)
from app.providers.publishing import LocalPublishingProvider
from app.schemas.publish_job import PublishJobResponse, PublishJobStatusResponse
from app.services import publishing_service as publishing_service_module
from app.services.publishing_service import PublishingService
from app.tasks import publishing as publishing_task


class WorkflowState:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.render = VideoRender(
            id=7,
            script_id=4,
            voice_track_id=8,
            status=VideoRenderStatus.COMPLETED,
            output_format=RenderOutputFormat.MP4,
            video_codec=VideoCodec.H264,
            audio_codec=RenderAudioCodec.AAC,
            resolution_preset=ResolutionPreset.VERTICAL_1080X1920,
            width=1080,
            height=1920,
            fps=30,
            fit_mode=RenderFitMode.COVER,
            background_color="#000000",
            subtitle_enabled=True,
            subtitle_style={},
            render_options={},
            timeline_data=[],
            storage_key="renders/7/output.mp4",
            checksum="sha256:original-render",
            file_size_bytes=4096,
            duration_seconds=30.0,
            completed_at=now,
        )
        self.jobs: dict[int, PublishJob] = {}
        self.next_job_id = 1
        self.events: list[str] = []
        self.saved_statuses: list[tuple[int, PublishStatus]] = []


class InMemorySession:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closes += 1

    async def commit(self) -> None:
        self.commits += 1
        self.state.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.state.events.append("rollback")


class InMemoryVideoRenderRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, render_id: int) -> VideoRender | None:
        return self.state.render if self.state.render.id == render_id else None


class InMemoryPublishJobRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.state = session.state

    async def create(self, publish_job: PublishJob) -> PublishJob:
        publish_job.id = self.state.next_job_id
        self.state.next_job_id += 1
        now = datetime.now(UTC)
        publish_job.created_at = now
        publish_job.updated_at = now
        self.state.jobs[publish_job.id] = publish_job
        self.state.events.append(f"create:{publish_job.id}")
        return publish_job

    async def get(self, publish_job_id: int) -> PublishJob | None:
        return self.state.jobs.get(publish_job_id)

    async def get_by_video_render_id(self, video_render_id: int) -> list[PublishJob]:
        jobs = [job for job in self.state.jobs.values() if job.video_render_id == video_render_id]
        return sorted(jobs, key=lambda job: (job.created_at, job.id), reverse=True)

    async def save(self, publish_job: PublishJob) -> PublishJob:
        publish_job.updated_at = datetime.now(UTC)
        self.state.jobs[publish_job.id] = publish_job
        self.state.saved_statuses.append((publish_job.id, publish_job.status))
        self.state.events.append(f"save:{publish_job.id}:{publish_job.status.value}")
        return publish_job

    async def commit(self) -> None:
        await self.session.commit()


class CountingLocalPublishingProvider(LocalPublishingProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.inputs = []

    async def publish(self, publishing_input):
        self.calls += 1
        self.inputs.append(publishing_input.model_copy(deep=True))
        return await super().publish(publishing_input)


class FailingPublishingProvider:
    async def publish(self, publishing_input):
        raise RuntimeError("controlled publishing failure")


class Phase8Harness:
    def __init__(self, monkeypatch) -> None:
        self.state = WorkflowState()
        self.session = InMemorySession(self.state)
        self.provider = CountingLocalPublishingProvider()
        self.queued: list[int] = []
        self.publication_commits: list[int] = []
        self.render_repository = InMemoryVideoRenderRepository(self.session)
        self.job_repository = InMemoryPublishJobRepository(self.session)
        self.service = self._service(self.provider)

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_publishing_service] = lambda: self.service

        def publish(publish_job_id: int) -> None:
            assert self.state.events[-1] == "commit"
            self.publication_commits.append(self.session.commits)
            self.queued.append(publish_job_id)

        monkeypatch.setattr(publishing_endpoint.execute_publish, "delay", publish)
        monkeypatch.setattr(publishing_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(publishing_task, "VideoRenderRepository", InMemoryVideoRenderRepository)
        monkeypatch.setattr(publishing_task, "PublishJobRepository", InMemoryPublishJobRepository)
        monkeypatch.setattr(publishing_task, "LocalPublishingProvider", lambda: self.provider)

    def _service(self, provider) -> PublishingService:
        return PublishingService(self.render_repository, self.job_repository, provider)

    def client(self) -> TestClient:
        return TestClient(self.app)

    def run_task(self, publish_job_id: int) -> dict[str, int | str | None]:
        return asyncio.run(publishing_task._run_publishing(publish_job_id))


def publish_request(
    *,
    title: str = "A finished short",
    account: str = "channel-primary",
    visibility: str = "private",
    scheduled_publish_at: datetime | None = None,
) -> dict:
    return {
        "metadata": {
            "title": title,
            "description": "A deterministic publishing workflow.",
            "tags": ["ClipForge", "shorts", "clipforge"],
            "category": "education",
        },
        "options": {
            "platform": "youtube",
            "account_reference": account,
            "visibility": visibility,
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": (
                scheduled_publish_at.isoformat() if scheduled_publish_at else None
            ),
        },
    }


def snapshot(job: PublishJob) -> tuple[str, str | None, int, float]:
    return (
        job.source_storage_key,
        job.source_checksum,
        job.source_file_size_bytes,
        job.source_duration_seconds,
    )


def test_immediate_workflow_snapshot_variants_and_idempotency(monkeypatch) -> None:
    harness = Phase8Harness(monkeypatch)

    with harness.client() as client:
        created_response = client.post("/api/v1/renders/7/publish-jobs", json=publish_request())
        created = PublishJobStatusResponse.model_validate(created_response.json())
        assert created_response.status_code == 202
        assert created.status == PublishStatus.PENDING
        assert harness.queued == [created.id]
        assert harness.publication_commits == [1]
        original_snapshot = snapshot(harness.state.jobs[created.id])
        assert original_snapshot == (
            "renders/7/output.mp4",
            "sha256:original-render",
            4096,
            30.0,
        )

        harness.state.render.storage_key = "renders/7/replaced.mp4"
        harness.state.render.checksum = "sha256:replacement"
        harness.state.render.file_size_bytes = 9999
        harness.state.render.duration_seconds = 99.0
        task_result = harness.run_task(harness.queued.pop())

        published_job = harness.state.jobs[created.id]
        assert task_result == {
            "publish_job_id": created.id,
            "publish_status": "published",
            "remote_media_id": published_job.remote_media_id,
        }
        assert published_job.status == PublishStatus.PUBLISHED
        assert snapshot(published_job) == original_snapshot
        assert harness.provider.inputs[0].source_storage_key == original_snapshot[0]
        assert harness.provider.inputs[0].source_checksum == original_snapshot[1]
        assert str(published_job.remote_url).startswith(
            "https://publishing.local.invalid/youtube/local-youtube-"
        )
        assert "youtube.com" not in published_job.remote_url
        assert published_job.remote_status == "published"
        assert published_job.provider_metadata["synthetic"] is True
        assert published_job.provider_metadata["real_publication"] is False
        assert published_job.published_at.tzinfo is not None
        assert published_job.completed_at.tzinfo is not None

        completed_response = client.get(f"/api/v1/publish-jobs/{created.id}")
        completed = PublishJobResponse.model_validate(completed_response.json())
        assert completed_response.status_code == 200
        assert completed.remote_media_id == published_job.remote_media_id

        harness.state.render.storage_key = original_snapshot[0]
        harness.state.render.checksum = original_snapshot[1]
        harness.state.render.file_size_bytes = original_snapshot[2]
        harness.state.render.duration_seconds = original_snapshot[3]
        second_response = client.post(
            "/api/v1/renders/7/publish-jobs",
            json=publish_request(
                title="A second publication",
                account="channel-secondary",
                visibility="unlisted",
            ),
        )
        second = PublishJobStatusResponse.model_validate(second_response.json())
        assert second_response.status_code == 202
        assert second.id != created.id
        assert len(harness.state.jobs) == 2
        second_job = harness.state.jobs[second.id]
        assert second_job.account_reference == "channel-secondary"
        assert second_job.visibility.value == "unlisted"
        assert second_job.tags == ["ClipForge", "shorts"]

        mixed_response = client.get("/api/v1/renders/7/publish-jobs")
        assert mixed_response.status_code == 200
        assert [item["id"] for item in mixed_response.json()] == [second.id, created.id]
        PublishJobStatusResponse.model_validate(mixed_response.json()[0])
        PublishJobResponse.model_validate(mixed_response.json()[1])

        harness.run_task(harness.queued.pop())
        assert second_job.remote_media_id != published_job.remote_media_id
        preserved_remote = (
            published_job.remote_media_id,
            published_job.remote_url,
            published_job.published_at,
            dict(published_job.provider_metadata),
        )
        calls_before_rerun = harness.provider.calls
        harness.run_task(created.id)
        assert harness.provider.calls == calls_before_rerun
        assert (
            published_job.remote_media_id,
            published_job.remote_url,
            published_job.published_at,
            published_job.provider_metadata,
        ) == preserved_remote

        queued_before_retry = list(harness.queued)
        retry = client.post(f"/api/v1/publish-jobs/{created.id}/retry")
        assert retry.status_code == 200
        PublishJobResponse.model_validate(retry.json())
        assert harness.queued == queued_before_retry
        assert len(harness.state.jobs) == 2


def test_failure_recovery_reuses_job_and_preserves_intent(monkeypatch) -> None:
    harness = Phase8Harness(monkeypatch)

    with harness.client() as client:
        created = PublishJobStatusResponse.model_validate(
            client.post("/api/v1/renders/7/publish-jobs", json=publish_request()).json()
        )
        harness.queued.clear()
        job = harness.state.jobs[created.id]
        original_snapshot = snapshot(job)
        original_intent = (job.title, list(job.tags), dict(job.publish_options))
        harness.provider = FailingPublishingProvider()

        with pytest.raises(PublishingError):
            harness.run_task(created.id)

        assert job.status == PublishStatus.FAILED
        assert job.completed_at is None
        assert job.error_message == "controlled publishing failure"
        assert harness.state.events[-1] == "commit"
        failed = PublishJobStatusResponse.model_validate(
            client.get(f"/api/v1/publish-jobs/{created.id}").json()
        )
        assert failed.status == PublishStatus.FAILED

        retry_response = client.post(f"/api/v1/publish-jobs/{created.id}/retry")
        retry = PublishJobStatusResponse.model_validate(retry_response.json())
        assert retry_response.status_code == 202
        assert retry.id == created.id
        assert retry.error_message is None
        assert snapshot(job) == original_snapshot
        assert (job.title, job.tags, job.publish_options) == original_intent
        assert len(harness.state.jobs) == 1

        harness.provider = CountingLocalPublishingProvider()
        recovered = harness.run_task(harness.queued.pop())
        assert recovered["publish_status"] == "published"
        assert job.status == PublishStatus.PUBLISHED
        assert snapshot(job) == original_snapshot
        assert len(harness.state.jobs) == 1


def test_future_schedule_waits_then_same_job_publishes_when_clock_is_due(
    monkeypatch,
) -> None:
    harness = Phase8Harness(monkeypatch)
    scheduled = datetime.now(UTC) + timedelta(days=2)

    with harness.client() as client:
        response = client.post(
            "/api/v1/renders/7/publish-jobs",
            json=publish_request(scheduled_publish_at=scheduled),
        )
        pending = PublishJobStatusResponse.model_validate(response.json())
        assert response.status_code == 202
        assert pending.status == PublishStatus.PENDING
        assert pending.scheduled_publish_at == scheduled
        assert harness.queued == []
        assert harness.session.commits == 1
        assert harness.provider.calls == 0

        class DueClock(datetime):
            @classmethod
            def now(cls, tz=None):
                due = scheduled + timedelta(seconds=1)
                return due if tz is not None else due.replace(tzinfo=None)

        monkeypatch.setattr(publishing_service_module, "datetime", DueClock)
        from app.providers.publishing import local as local_provider_module

        monkeypatch.setattr(local_provider_module, "datetime", DueClock)
        retry = client.post(f"/api/v1/publish-jobs/{pending.id}/retry")
        assert retry.status_code == 202
        assert harness.queued == [pending.id]
        result = harness.run_task(harness.queued.pop())
        assert result["publish_status"] == "published"
        job = harness.state.jobs[pending.id]
        assert job.published_at == scheduled
        assert job.published_at.tzinfo is not None
        assert harness.provider.calls == 1

        past = scheduled - timedelta(days=5)
        due_response = client.post(
            "/api/v1/renders/7/publish-jobs",
            json=publish_request(title="Already due", scheduled_publish_at=past),
        )
        assert due_response.status_code == 202
        assert harness.queued == [due_response.json()["id"]]


def test_cancellation_conflicts_are_isolated_and_never_publish(monkeypatch) -> None:
    harness = Phase8Harness(monkeypatch)
    future = datetime.now(UTC) + timedelta(days=1)

    with harness.client() as client:
        pending = PublishJobStatusResponse.model_validate(
            client.post(
                "/api/v1/renders/7/publish-jobs",
                json=publish_request(scheduled_publish_at=future),
            ).json()
        )
        job = harness.state.jobs[pending.id]
        original_snapshot = snapshot(job)
        cancelled_response = client.post(f"/api/v1/publish-jobs/{pending.id}/cancel")
        cancelled = PublishJobStatusResponse.model_validate(cancelled_response.json())
        assert cancelled_response.status_code == 200
        assert cancelled.status == PublishStatus.CANCELLED
        assert snapshot(job) == original_snapshot
        assert client.post(f"/api/v1/publish-jobs/{pending.id}/cancel").status_code == 200
        assert client.post(f"/api/v1/publish-jobs/{pending.id}/retry").status_code == 409
        assert harness.queued == []
        assert harness.provider.calls == 0

        job.status = PublishStatus.PUBLISHING
        assert client.post(f"/api/v1/publish-jobs/{pending.id}/cancel").status_code == 409
        job.status = PublishStatus.PUBLISHED
        job.remote_media_id = "local-youtube-existing"
        job.published_at = datetime.now(UTC)
        job.completed_at = datetime.now(UTC)
        assert client.post(f"/api/v1/publish-jobs/{pending.id}/cancel").status_code == 409
        assert snapshot(job) == original_snapshot

        job.status = PublishStatus.FAILED
        job.remote_media_id = None
        job.published_at = None
        job.completed_at = None
        failed_cancel = client.post(f"/api/v1/publish-jobs/{pending.id}/cancel")
        assert failed_cancel.status_code == 200
        assert failed_cancel.json()["status"] == "cancelled"
        assert harness.provider.calls == 0


def test_broker_failures_persist_failed_state_without_provider_call(monkeypatch) -> None:
    harness = Phase8Harness(monkeypatch)

    def fail_publish(publish_job_id: int) -> None:
        assert harness.state.events[-1] == "commit"
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(publishing_endpoint.execute_publish, "delay", fail_publish)
    with harness.client() as client:
        response = client.post("/api/v1/renders/7/publish-jobs", json=publish_request())
        assert response.status_code == 503
        assert response.json() == {"detail": "Publishing could not be queued"}
        assert len(harness.state.jobs) == 1
        job = next(iter(harness.state.jobs.values()))
        assert job.status == PublishStatus.FAILED
        assert job.completed_at is None
        assert job.error_message == "Publishing task enqueue failed: broker unavailable"
        assert harness.provider.calls == 0
        assert harness.state.events[-2:] == [f"save:{job.id}:failed", "commit"]

        retry = client.post(f"/api/v1/publish-jobs/{job.id}/retry")
        assert retry.status_code == 503
        assert len(harness.state.jobs) == 1
        assert job.status == PublishStatus.FAILED
        assert harness.provider.calls == 0
