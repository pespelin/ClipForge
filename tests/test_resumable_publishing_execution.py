from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import (
    PublishingError,
    PublishingExecutionLeaseLostError,
    PublishingExecutionLeaseUnavailableError,
    PublishingExecutionLockUnavailableError,
    PublishingTransientError,
)
from app.models.publish_job import PublishJob, PublishPlatform, PublishStatus
from app.providers.publishing import ResumablePublishingSession
from app.schemas.publish_job import PublishingInput
from app.services.publishing_service import PublishingService
from app.services.publishing_upload_session_service import PublishingUploadSessionData

SESSION_URI = "https://upload.youtube.test/secret-session-capability"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeRepository:
    def __init__(self, row=None, events=None) -> None:
        self.row = row
        self.saved_statuses = []
        self.normal_gets = []
        self.locked_gets = []
        self.events = events if events is not None else []
        self.lock_error: Exception | None = None

    async def get(self, row_id: int):
        self.normal_gets.append(row_id)
        return self.row if self.row and self.row.id == row_id else None

    async def get_for_update(self, row_id: int):
        self.events.append("lock")
        self.locked_gets.append(row_id)
        if self.lock_error is not None:
            raise self.lock_error
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
        self.execution_owner: str | None = None
        self.execution_lease_expires_at: datetime | None = None

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
        self.execution_owner = None
        self.execution_lease_expires_at = None
        return True

    async def acquire_execution_lease(
        self,
        publish_job_id: int,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ):
        self.events.append("lease_acquire")
        if (
            self.execution_owner is not None
            and self.execution_lease_expires_at is not None
            and self.execution_lease_expires_at > now
        ):
            raise PublishingExecutionLeaseUnavailableError
        self.execution_owner = owner
        self.execution_lease_expires_at = lease_expires_at
        return object()

    async def release_execution_lease(self, publish_job_id: int, *, owner: str):
        self.events.append("lease_release")
        if self.execution_owner != owner:
            return False
        self.execution_owner = None
        self.execution_lease_expires_at = None
        return True

    async def renew_execution_lease(
        self,
        publish_job_id: int,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ):
        self.events.append("lease_renew")
        if (
            self.execution_owner != owner
            or self.execution_lease_expires_at is None
            or self.execution_lease_expires_at <= now
        ):
            raise PublishingExecutionLeaseLostError
        self.execution_lease_expires_at = lease_expires_at
        return object()


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
        execution_guard=None,
    ):
        if execution_guard is not None:
            await execution_guard.renew()
        self.events.append("media")
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


def make_service(job=None, checkpoint=None, *, owner="task-owner-a"):
    events = []
    repository = FakeRepository(job or publish_job(), events)
    provider = FakeResumableProvider(events)
    sessions = FakeUploadSessionService(checkpoint, events)

    async def persist_renewal() -> None:
        events.append("renewal_commit")

    service = PublishingService(
        object(),
        repository,
        provider,
        sessions,
        execution_owner=owner,
        execution_lease_seconds=900,
        clock=lambda: NOW,
        execution_checkpoint=persist_renewal,
    )
    return service, repository, provider, sessions


async def test_new_session_prepare_stores_checkpoint_without_media_transfer() -> None:
    service, repository, provider, sessions = make_service()

    plan = await service.prepare_publish_job_execution(7)

    assert plan.requires_pre_execution_commit is True
    assert plan.checkpoint_created is True
    assert repository.locked_gets == [7]
    assert repository.normal_gets == []
    assert repository.events[:5] == [
        "lock",
        "checkpoint_lookup",
        "initiate",
        "checkpoint_store",
        "lease_acquire",
    ]
    assert len(provider.initiated) == 1
    assert provider.resumed == []
    assert len(sessions.stored) == 1
    checkpoint = sessions.stored[0]
    assert checkpoint.publish_job_id == 7
    assert checkpoint.platform is PublishPlatform.YOUTUBE
    assert checkpoint.session_uri == SESSION_URI
    assert checkpoint.total_bytes == 4096
    assert sessions.execution_owner == "task-owner-a"
    assert sessions.execution_lease_expires_at == NOW + timedelta(seconds=900)


async def test_lock_contention_does_not_mutate_job_checkpoint_or_provider() -> None:
    service, repository, provider, sessions = make_service()
    repository.lock_error = PublishingExecutionLockUnavailableError()

    with pytest.raises(PublishingExecutionLockUnavailableError):
        await service.prepare_publish_job_execution(7)

    assert repository.row.status is PublishStatus.PENDING
    assert repository.saved_statuses == []
    assert repository.events == ["lock"]
    assert sessions.stored == []
    assert provider.initiated == []
    assert provider.resumed == []


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
    assert repository.events[:3] == ["lock", "checkpoint_lookup", "lease_acquire"]
    assert provider.initiated == []
    assert sessions.stored == []
    assert plan.resumable_session.next_byte_offset == 1024


async def test_active_other_owner_lease_blocks_media_without_job_mutation() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096, 1024)
    service, repository, provider, sessions = make_service(
        checkpoint=checkpoint,
        owner="task-owner-b",
    )
    sessions.execution_owner = "task-owner-a"
    sessions.execution_lease_expires_at = NOW + timedelta(seconds=60)

    with pytest.raises(PublishingExecutionLeaseUnavailableError):
        await service.prepare_publish_job_execution(7)

    assert repository.row.status is PublishStatus.PENDING
    assert repository.saved_statuses == []
    assert repository.events == ["lock", "checkpoint_lookup", "lease_acquire"]
    assert provider.initiated == []
    assert provider.resumed == []
    assert sessions.execution_owner == "task-owner-a"


@pytest.mark.parametrize("duplicate_owner", ["task-owner-a", "task-owner-b"])
async def test_duplicate_delivery_during_active_upload_is_deferred_without_mutation(
    duplicate_owner: str,
) -> None:
    checkpoint = PublishingUploadSessionData(
        7,
        PublishPlatform.YOUTUBE,
        SESSION_URI,
        4096,
        1024,
    )
    job = publish_job(PublishStatus.PUBLISHING)
    service, repository, provider, sessions = make_service(
        job=job,
        checkpoint=checkpoint,
        owner=duplicate_owner,
    )
    original_expiry = NOW + timedelta(seconds=60)
    sessions.execution_owner = "task-owner-a"
    sessions.execution_lease_expires_at = original_expiry

    with pytest.raises(PublishingExecutionLeaseUnavailableError):
        await service.prepare_publish_job_execution(7)

    assert job.status is PublishStatus.PUBLISHING
    assert repository.saved_statuses == []
    assert provider.initiated == []
    assert provider.resumed == []
    assert sessions.checkpoint is checkpoint
    assert sessions.stored == []
    assert sessions.deleted == []
    assert sessions.execution_owner == "task-owner-a"
    assert sessions.execution_lease_expires_at == original_expiry


async def test_duplicate_delivery_after_publication_is_idempotent_no_op() -> None:
    job = publish_job(PublishStatus.PUBLISHED)
    service, repository, provider, sessions = make_service(job=job, owner="task-owner-b")

    plan = await service.prepare_publish_job_execution(7)
    result = await service.execute_prepared_publish(plan)

    assert plan.already_complete is True
    assert result is job
    assert job.status is PublishStatus.PUBLISHED
    assert repository.saved_statuses == []
    assert provider.initiated == []
    assert provider.resumed == []
    assert sessions.stored == []
    assert sessions.deleted == []
    assert sessions.events == ["lock"]


async def test_expired_lease_allows_new_owner_to_resume() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096, 1024)
    service, _, provider, sessions = make_service(
        checkpoint=checkpoint,
        owner="task-owner-b",
    )
    sessions.execution_owner = "task-owner-a"
    sessions.execution_lease_expires_at = NOW

    plan = await service.prepare_publish_job_execution(7)

    assert plan.execution_owner == "task-owner-b"
    assert sessions.execution_owner == "task-owner-b"
    assert provider.initiated == []


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
    assert sessions.execution_owner is None
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
    assert sessions.execution_owner is None
    assert plan.publish_job.status is PublishStatus.FAILED


async def test_old_worker_stops_when_new_owner_has_taken_over() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096)
    service, repository, provider, sessions = make_service(checkpoint=checkpoint)
    plan = await service.prepare_publish_job_execution(7)
    sessions.execution_owner = "task-owner-b"
    sessions.execution_lease_expires_at = NOW + timedelta(seconds=900)

    with pytest.raises(PublishingExecutionLeaseLostError):
        await service.execute_prepared_publish(plan)

    assert provider.resumed == []
    assert sessions.execution_owner == "task-owner-b"
    assert sessions.deleted == []
    assert plan.publish_job.status is PublishStatus.PUBLISHING
    assert repository.events[-1] == "lease_renew"


async def test_transient_failure_retains_checkpoint_and_retry_classification() -> None:
    checkpoint = PublishingUploadSessionData(7, PublishPlatform.YOUTUBE, SESSION_URI, 4096)
    service, _, provider, sessions = make_service(checkpoint=checkpoint)
    provider.error = PublishingTransientError()
    plan = await service.prepare_publish_job_execution(7)

    with pytest.raises(PublishingTransientError):
        await service.execute_prepared_publish(plan)

    assert sessions.deleted == []
    assert sessions.checkpoint is checkpoint
    assert sessions.execution_owner is None
    assert plan.publish_job.status is PublishStatus.FAILED


async def test_duplicate_delivery_after_failure_reuses_checkpoint_without_reinitiation() -> None:
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
    assert first_plan.checkpoint_created is True
    assert second_plan.checkpoint_created is False
    assert second_plan.resumable_session.session_uri == SESSION_URI
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
