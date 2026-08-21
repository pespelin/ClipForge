import logging
from datetime import UTC, datetime

import pytest

from app.core.exceptions import PublishingTransientError, PublishJobNotFoundError
from app.models.publish_job import PublishJob, PublishPlatform, PublishStatus
from app.providers.publishing import (
    PublishingReconciliationResult,
    PublishingRemoteState,
)
from app.schemas.publish_job import PublishingResult
from app.services.publishing_reconciliation_service import (
    PublishingReconciliationOutcome,
    PublishingReconciliationService,
)
from app.services.publishing_upload_session_service import PublishingUploadSessionData

SESSION_URI = "https://upload.youtube.test/service-secret-session"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def publish_job(status=PublishStatus.FAILED, **values) -> PublishJob:
    defaults = {
        "id": 7,
        "video_render_id": 4,
        "status": status,
        "platform": PublishPlatform.YOUTUBE,
        "account_reference": "channel-main",
        "title": "Publish deliberately",
        "description": "A useful Short.",
        "tags": ["Editing"],
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
        "error_message": "previous safe failure",
    }
    defaults.update(values)
    return PublishJob(**defaults)


class Repository:
    def __init__(self, row=None) -> None:
        self.row = row
        self.saved = []

    async def get(self, row_id: int):
        return self.row if self.row is not None and self.row.id == row_id else None

    async def save(self, row):
        self.saved.append(row)
        return row


class Sessions:
    def __init__(self, checkpoint=None) -> None:
        self.checkpoint = checkpoint
        self.stored = []
        self.deleted = []
        self.execution_owner = None
        self.execution_lease_expires_at = None
        self.lease_checks = []

    async def get_by_publish_job_id(self, publish_job_id: int):
        return (
            self.checkpoint
            if self.checkpoint and self.checkpoint.publish_job_id == publish_job_id
            else None
        )

    async def store(self, checkpoint):
        self.checkpoint = checkpoint
        self.stored.append(checkpoint)

    async def delete_by_publish_job_id(self, publish_job_id: int):
        self.deleted.append(publish_job_id)
        self.checkpoint = None
        return True

    async def is_execution_lease_active(self, publish_job_id: int, *, now: datetime):
        self.lease_checks.append((publish_job_id, now))
        return bool(
            self.checkpoint is not None
            and self.execution_owner is not None
            and self.execution_lease_expires_at is not None
            and self.execution_lease_expires_at > now
        )


class Provider:
    def __init__(self, result=None, error=None) -> None:
        self.result = result or PublishingReconciliationResult(PublishingRemoteState.UNKNOWN)
        self.error = error
        self.inputs = []

    async def reconcile(self, reconciliation_input):
        self.inputs.append(reconciliation_input)
        if self.error is not None:
            raise self.error
        return self.result


def checkpoint(offset=0):
    return PublishingUploadSessionData(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        session_uri=SESSION_URI,
        total_bytes=4096,
        next_byte_offset=offset,
    )


def published_result():
    return PublishingResult(
        remote_media_id="recovered-video-123",
        remote_url="https://www.youtube.com/watch?v=recovered-video-123",
        remote_status="processed",
        published_at=NOW,
        provider_metadata={"provider": "youtube", "reconciled": True},
    )


def make_service(row=None, session=None, result=None, error=None):
    repository = Repository(row)
    sessions = Sessions(session)
    provider = Provider(result, error)
    service = PublishingReconciliationService(
        repository,
        provider,
        sessions,
        clock=lambda: NOW,
    )
    return service, repository, provider, sessions


async def test_missing_publish_job_is_controlled_error() -> None:
    service, _, provider, _ = make_service()
    with pytest.raises(PublishJobNotFoundError):
        await service.reconcile(7)
    assert provider.inputs == []


@pytest.mark.parametrize(
    "status", [PublishStatus.PUBLISHED, PublishStatus.CANCELLED, PublishStatus.PENDING]
)
async def test_non_reconcilable_local_state_is_idempotent_noop(status) -> None:
    service, repository, provider, sessions = make_service(publish_job(status))
    outcome = await service.reconcile(7)
    assert outcome.changed is False
    assert outcome.remote_state is PublishingRemoteState.UNKNOWN
    assert provider.inputs == []
    assert repository.saved == []
    assert sessions.deleted == []
    assert sessions.lease_checks == []


@pytest.mark.parametrize("status", [PublishStatus.FAILED, PublishStatus.PUBLISHING])
async def test_active_execution_lease_defers_reconciliation_without_mutation(
    status, caplog
) -> None:
    owner = "task-owner-secret-16d"
    token = "ACCESS_TOKEN_SECRET_16D"
    caplog.set_level(logging.INFO, logger="app.services.publishing_reconciliation_service")
    row = publish_job(status, remote_media_id="existing-video")
    service, repository, provider, sessions = make_service(row, checkpoint(100))
    sessions.execution_owner = owner
    sessions.execution_lease_expires_at = NOW.replace(minute=1)

    outcome = await service.reconcile(7)

    assert outcome.remote_state is PublishingRemoteState.EXECUTION_ACTIVE
    assert outcome.changed is False
    assert provider.inputs == []
    assert repository.saved == []
    assert sessions.stored == []
    assert sessions.deleted == []
    assert sessions.checkpoint.next_byte_offset == 100
    assert sessions.execution_owner == owner
    assert "publishing.reconciliation.execution_active publish_job_id=7" in caplog.text
    assert owner not in caplog.text
    assert token not in caplog.text
    assert SESSION_URI not in caplog.text
    assert "channel-main" not in caplog.text


async def test_remote_media_id_is_preferred_and_mapped_without_session_exposure() -> None:
    row = publish_job(remote_media_id="existing-video")
    service, _, provider, _ = make_service(row, checkpoint(100))
    outcome = await service.reconcile(7)
    received = provider.inputs[0]
    assert received.remote_media_id == "existing-video"
    assert received.account_reference == "channel-main"
    assert received.platform is PublishPlatform.YOUTUBE
    assert received.resumable_session is not None
    assert SESSION_URI not in repr(received)
    assert outcome.changed is False


async def test_remote_media_id_without_checkpoint_reconciles_without_lease_lookup() -> None:
    row = publish_job(remote_media_id="existing-video")
    service, _, provider, sessions = make_service(row)

    await service.reconcile(7)

    assert len(provider.inputs) == 1
    assert sessions.lease_checks == []


async def test_checkpoint_without_remote_id_maps_resumable_session() -> None:
    service, _, provider, _ = make_service(publish_job(), checkpoint(512))
    await service.reconcile(7)
    received = provider.inputs[0]
    assert received.remote_media_id is None
    assert received.resumable_session is not None
    assert received.resumable_session.total_bytes == 4096
    assert received.resumable_session.next_byte_offset == 512


async def test_no_remote_reference_returns_unknown_without_provider_call() -> None:
    service, repository, provider, _ = make_service(publish_job())
    outcome = await service.reconcile(7)
    assert outcome.remote_state is PublishingRemoteState.UNKNOWN
    assert outcome.changed is False
    assert provider.inputs == []
    assert repository.saved == []


@pytest.mark.parametrize("status", [PublishStatus.FAILED, PublishStatus.PUBLISHING])
async def test_remote_completion_recovers_job_and_deletes_checkpoint(status, caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.services.publishing_reconciliation_service")
    result = PublishingReconciliationResult(
        PublishingRemoteState.PUBLISHED,
        published_result(),
    )
    row = publish_job(status)
    service, repository, _, sessions = make_service(row, checkpoint(1024), result)
    sessions.execution_owner = "expired-owner"
    sessions.execution_lease_expires_at = NOW

    outcome = await service.reconcile(7)

    assert outcome.changed is True
    assert row.status is PublishStatus.PUBLISHED
    assert row.remote_media_id == "recovered-video-123"
    assert row.remote_status == "processed"
    assert row.published_at == NOW
    assert row.completed_at is not None
    assert row.error_message is None
    assert row.provider_metadata == {"provider": "youtube", "reconciled": True}
    assert sessions.deleted == [7]
    assert repository.saved == [row]
    assert "publishing.reconciliation.started publish_job_id=7" in caplog.text
    assert "publishing.reconciliation.published_recovered publish_job_id=7" in caplog.text
    assert SESSION_URI not in caplog.text
    assert "channel-main" not in caplog.text


async def test_incomplete_upload_updates_offset_and_retains_checkpoint(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.services.publishing_reconciliation_service")
    result = PublishingReconciliationResult(
        PublishingRemoteState.INCOMPLETE,
        next_byte_offset=2048,
    )
    row = publish_job()
    service, repository, _, sessions = make_service(row, checkpoint(1024), result)
    sessions.execution_owner = "expired-owner"
    sessions.execution_lease_expires_at = NOW.replace(year=2029)
    outcome = await service.reconcile(7)
    assert outcome.changed is True
    assert row.status is PublishStatus.FAILED
    assert sessions.deleted == []
    assert sessions.stored[0].next_byte_offset == 2048
    assert repository.saved == []
    assert "publishing.reconciliation.incomplete publish_job_id=7" in caplog.text
    assert "remote_state=incomplete" in caplog.text
    assert SESSION_URI not in caplog.text


@pytest.mark.parametrize(
    "remote_state", [PublishingRemoteState.NOT_FOUND, PublishingRemoteState.UNKNOWN]
)
async def test_not_found_or_unknown_preserves_local_state_and_checkpoint(
    remote_state, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.publishing_reconciliation_service")
    row = publish_job()
    result = PublishingReconciliationResult(remote_state)
    service, repository, _, sessions = make_service(row, checkpoint(100), result)
    outcome = await service.reconcile(7)
    assert outcome.changed is False
    assert row.status is PublishStatus.FAILED
    assert sessions.checkpoint is not None
    assert sessions.deleted == []
    assert repository.saved == []
    event = "not_found" if remote_state is PublishingRemoteState.NOT_FOUND else "unknown"
    assert f"publishing.reconciliation.{event} publish_job_id=7" in caplog.text
    assert SESSION_URI not in caplog.text


async def test_transient_provider_failure_has_no_destructive_local_mutation(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.services.publishing_reconciliation_service")
    row = publish_job()
    error = PublishingTransientError()
    service, repository, _, sessions = make_service(row, checkpoint(100), error=error)
    with pytest.raises(PublishingTransientError):
        await service.reconcile(7)
    assert row.status is PublishStatus.FAILED
    assert row.error_message == "previous safe failure"
    assert sessions.deleted == []
    assert sessions.stored == []
    assert repository.saved == []
    assert "publishing.reconciliation.failed publish_job_id=7" in caplog.text
    assert "failure_category=transient" in caplog.text
    assert SESSION_URI not in caplog.text


def test_outcome_and_input_repr_do_not_expose_session_uri() -> None:
    row = publish_job()
    outcome = PublishingReconciliationOutcome(row, PublishingRemoteState.UNKNOWN, False)
    assert SESSION_URI not in repr(checkpoint())
    assert SESSION_URI not in repr(outcome)
