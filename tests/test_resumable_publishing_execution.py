from datetime import UTC, datetime

import pytest

from app.core.exceptions import PublishingError, PublishingTransientError
from app.models.publish_job import PublishJob, PublishPlatform, PublishStatus
from app.providers.publishing import ResumablePublishingSession
from app.schemas.publish_job import PublishingInput
from app.services.publishing_service import PublishingService
from app.services.publishing_upload_session_service import PublishingUploadSessionData

SESSION_URI = "https://upload.youtube.test/secret-session-capability"


class FakeRepository:
    def __init__(self, row=None, events=None) -> None:
        self.row = row
        self.saved_statuses = []
        self.normal_gets = []
        self.locked_gets = []
        self.events = events if events is not None else []

    async def get(self, row_id: int):
        self.normal_gets.append(row_id)
        return self.row if self.row and self.row.id == row_id else None

    async def get_for_update(self, row_id: int):
        self.events.append("lock")
        self.locked_gets.append(row_id)
        return self.row if self.row and self.row.id == row_id else None

    async def save(self, row):
        self.row = row
        self.saved_statuses.append(row.status)
        return row


class FakeUploadSessionService:
    def __init__(
        self,
        checkpoint: PublishingUploadSessionData | None = None,
        events=None,
    ) -> None:
        self.checkpoint = checkpoint
        self.stored = []
        self.deleted = []
        self.events = events if events is not None else []
        self.store_error: Exception | None = None

    async def get_by_publish_job_id(self, publish_job_id: int):
        self.events.append("checkpoint_lookup")
        if self.checkpoint and self.checkpoint.publish_job_id == publish_job_id:
            return self.checkpoint
        return None

    async def store(self, checkpoint: PublishingUploadSessionData):
        self.events.append("checkpoint_store")
        if self.store_error is not None:
            raise self.store_error
        self.checkpoint = checkpoint
        self.stored.append(checkpoint)
        return object()

    async def delete_by_publish_job_id(self, publish_job_id: int):
        self.deleted.append(publish_job_id)
        self.checkpoint = None
        return True


class FakeResumableProvider:
    def __init__(self, events=None) -> None:
        self.initiated = []
        self.resumed = []
        self.error: Exception | None = None
        self.initiation_error: Exception | None = None
        self.events = events if events is not None else []

    async def publish(self, publishing_input: PublishingInput):
        raise AssertionError("generic publish must not run for resumable capability")

    async def initiate_upload(self, publishing_input: PublishingInput):
        self.events.append("initiate")
        self.initiated.append(publishing_input)
        if self.initiation_error is not None:
            raise self.initiation_error
        return ResumablePublishingSession(SESSION_URI, 4096)

    async def resume_upload(
        self,
        publishing_input: PublishingInput,
        session: ResumablePublishingSession,
    ):
        self.resumed.append((publishing_input, session))
        if self.error is not None:
            raise self.error
        return {
            "remote_media_id": "youtube-123",
            "remote_url": "https://www.youtube.com/watch?v=youtube-123",
            "remote_status": "published",
            "published_at": datetime(2030, 1, 1, tzinfo=UTC),
            "provider_metadata": {"provider": "youtube", "upload_type": "resumable"},
        }


def publish_job(status=PublishStatus.PENDING) -> PublishJob:
    return PublishJob(
        id=7,
        video_render_id=4,
        status=status,
        platform=PublishPlatform.YOUTUBE,
        account_reference="channel-main",
        title="Publish deliberately",
        description="A useful Short.",
        tags=["Editing", "Shorts"],
        category="education",
        visibility="private",
        made_for_kids=False,
        notify_subscribers=False,
        language="en",
        publish_options={
            "platform": "youtube",
            "account_reference": "channel-main",
            "visibility": "private",
            "made_for_kids": False,
            "notify_subscribers": False,
            "language": "en",
            "scheduled_publish_at": None,
        },
        source_storage_key="renders/4/output.mp4",
        source_checksum="sha256:original",
        source_file_size_bytes=4096,
        source_duration_seconds=15,
        provider_metadata={},
    )


def make_service(job=None, checkpoint=None):
    events = []
    repository = FakeRepository(job or publish_job(), events)
    provider = FakeResumableProvider(events)
    sessions = FakeUploadSessionService(checkpoint, events)
    service = PublishingService(object(), repository, provider, sessions)
    return service, repository, provider, sessions


async def test_new_session_prepare_stores_checkpoint_without_media_transfer() -> None:
    service, repository, provider, sessions = make_service()

    plan = await service.prepare_publish_job_execution(7)

    assert plan.requires_pre_execution_commit is True
    assert plan.checkpoint_created is True
    assert repository.locked_gets == [7]
    assert repository.normal_gets == []
    assert repository.events[:4] == [
        "lock",
        "checkpoint_lookup",
        "initiate",
        "checkpoint_store",
    ]
    assert len(provider.initiated) == 1
    assert provider.resumed == []
    assert len(sessions.stored) == 1
    checkpoint = sessions.stored[0]
    assert checkpoint.publish_job_id == 7
    assert checkpoint.platform is PublishPlatform.YOUTUBE
    assert checkpoint.session_uri == SESSION_URI
    assert checkpoint.total_bytes == 4096


async def test_existing_checkpoint_is_reused_without_new_initiation() -> None:
    checkpoint = PublishingUploadSessionData(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        session_uri=SESSION_URI,
        total_bytes=4096,
        next_byte_offset=1024,
    )
    service, repository, provider, sessions = make_service(checkpoint=checkpoint)

    plan = await service.prepare_publish_job_execution(7)

    assert plan.requires_pre_execution_commit is True
    assert plan.checkpoint_created is False
    assert repository.locked_gets == [7]
    assert repository.normal_gets == []
    assert repository.events[:2] == ["lock", "checkpoint_lookup"]
    assert provider.initiated == []
    assert sessions.stored == []
    assert plan.resumable_session.next_byte_offset == 1024


async def test_initiation_failure_does_not_store_checkpoint_or_transfer_media() -> None:
    service, repository, provider, sessions = make_service()
    provider.initiation_error = RuntimeError("initiation failed")

    with pytest.raises(PublishingError):
        await service.prepare_publish_job_execution(7)

    assert repository.events == ["lock", "checkpoint_lookup", "initiate"]
    assert sessions.stored == []
    assert provider.resumed == []


async def test_checkpoint_flush_failure_does_not_transfer_media() -> None:
    service, repository, provider, sessions = make_service()
    sessions.store_error = RuntimeError("checkpoint flush failed")

    with pytest.raises(PublishingError):
        await service.prepare_publish_job_execution(7)

    assert repository.events == [
        "lock",
        "checkpoint_lookup",
        "initiate",
        "checkpoint_store",
    ]
    assert provider.resumed == []


async def test_execute_resumes_applies_result_and_cleans_checkpoint() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096, 1024)
    service, repository, provider, sessions = make_service(checkpoint=checkpoint)
    plan = await service.prepare_publish_job_execution(7)

    job = await service.execute_prepared_publish(plan)

    assert len(provider.resumed) == 1
    assert provider.resumed[0][1].session_uri == SESSION_URI
    assert provider.resumed[0][1].next_byte_offset == 1024
    assert sessions.deleted == [7]
    assert job.status is PublishStatus.PUBLISHED
    assert job.remote_media_id == "youtube-123"
    assert repository.saved_statuses == [
        PublishStatus.PUBLISHING,
        PublishStatus.PUBLISHED,
    ]


async def test_provider_failure_marks_failed_but_retains_checkpoint() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096)
    service, _, provider, sessions = make_service(checkpoint=checkpoint)
    provider.error = RuntimeError("temporary upload failure")
    plan = await service.prepare_publish_job_execution(7)

    with pytest.raises(PublishingError):
        await service.execute_prepared_publish(plan)

    assert sessions.deleted == []
    assert sessions.checkpoint is checkpoint
    assert plan.publish_job.status is PublishStatus.FAILED


async def test_transient_failure_retains_checkpoint_and_retry_classification() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096)
    service, _, provider, sessions = make_service(checkpoint=checkpoint)
    provider.error = PublishingTransientError()
    plan = await service.prepare_publish_job_execution(7)

    with pytest.raises(PublishingTransientError):
        await service.execute_prepared_publish(plan)

    assert sessions.deleted == []
    assert sessions.checkpoint is checkpoint
    assert plan.publish_job.status is PublishStatus.FAILED


async def test_retry_uses_durable_checkpoint_and_never_reinitiates() -> None:
    service, _, provider, sessions = make_service()
    first_plan = await service.prepare_publish_job_execution(7)
    provider.error = RuntimeError("interrupted")
    with pytest.raises(PublishingError):
        await service.execute_prepared_publish(first_plan)

    provider.error = None
    second_plan = await service.prepare_publish_job_execution(7)
    result = await service.execute_prepared_publish(second_plan)

    assert len(provider.initiated) == 1
    assert len(provider.resumed) == 2
    assert second_plan.requires_pre_execution_commit is True
    assert result.status is PublishStatus.PUBLISHED
    assert sessions.checkpoint is None


async def test_platform_mismatch_fails_without_initiation_or_resume() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.TIKTOK, SESSION_URI, 4096)
    service, _, provider, sessions = make_service(checkpoint=checkpoint)

    with pytest.raises(PublishingError):
        await service.prepare_publish_job_execution(7)

    assert provider.initiated == []
    assert provider.resumed == []
    assert sessions.deleted == []
