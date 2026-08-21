import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import PublishJobNotFoundError
from app.core.observability import publishing_failure_category
from app.models.publish_job import PublishJob, PublishStatus
from app.providers.publishing import (
    PublishingReconciliationInput,
    PublishingReconciliationProvider,
    PublishingRemoteState,
    ResumablePublishingSession,
)
from app.repositories.publish_job_repository import PublishJobRepository
from app.schemas.publish_job import PublishingResult
from app.services.publishing_upload_session_service import (
    PublishingUploadSessionData,
    PublishingUploadSessionService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublishingReconciliationOutcome:
    publish_job: PublishJob
    remote_state: PublishingRemoteState
    changed: bool


class PublishingReconciliationService:
    """Reconcile local publishing state against a provider-neutral remote view."""

    def __init__(
        self,
        publish_job_repository: PublishJobRepository,
        reconciliation_provider: PublishingReconciliationProvider,
        upload_session_service: PublishingUploadSessionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.publish_job_repository = publish_job_repository
        self.reconciliation_provider = reconciliation_provider
        self.upload_session_service = upload_session_service
        self.clock = clock or (lambda: datetime.now(UTC))

    async def reconcile(self, publish_job_id: int) -> PublishingReconciliationOutcome:
        logger.info("publishing.reconciliation.started publish_job_id=%s", publish_job_id)
        publish_job = await self.publish_job_repository.get(publish_job_id)
        if publish_job is None:
            logger.warning(
                "publishing.reconciliation.failed publish_job_id=%s failure_category=not_found",
                publish_job_id,
            )
            raise PublishJobNotFoundError
        if publish_job.status in {
            PublishStatus.PUBLISHED,
            PublishStatus.CANCELLED,
            PublishStatus.PENDING,
        }:
            return PublishingReconciliationOutcome(
                publish_job,
                PublishingRemoteState.UNKNOWN,
                False,
            )

        checkpoint = await self.upload_session_service.get_by_publish_job_id(publish_job.id)
        if checkpoint is not None and await self.upload_session_service.is_execution_lease_active(
            publish_job.id,
            now=self.clock(),
        ):
            logger.info(
                "publishing.reconciliation.execution_active publish_job_id=%s "
                "remote_state=execution_active",
                publish_job_id,
            )
            return PublishingReconciliationOutcome(
                publish_job,
                PublishingRemoteState.EXECUTION_ACTIVE,
                False,
            )
        if publish_job.remote_media_id is None and checkpoint is None:
            return PublishingReconciliationOutcome(
                publish_job,
                PublishingRemoteState.UNKNOWN,
                False,
            )
        try:
            result = await self.reconciliation_provider.reconcile(
                PublishingReconciliationInput(
                    platform=publish_job.platform,
                    account_reference=publish_job.account_reference,
                    visibility=publish_job.visibility,
                    remote_media_id=publish_job.remote_media_id,
                    resumable_session=self._session(checkpoint),
                )
            )
        except Exception as error:
            logger.warning(
                "publishing.reconciliation.failed publish_job_id=%s failure_category=%s",
                publish_job_id,
                publishing_failure_category(error),
            )
            raise
        if result.remote_state is PublishingRemoteState.PUBLISHED:
            if result.publishing_result is None:
                return PublishingReconciliationOutcome(publish_job, result.remote_state, False)
            self._apply_result(publish_job, result.publishing_result)
            publish_job.status = PublishStatus.PUBLISHED
            publish_job.completed_at = datetime.now(UTC)
            publish_job.error_message = None
            await self.upload_session_service.delete_by_publish_job_id(publish_job.id)
            await self.publish_job_repository.save(publish_job)
            logger.info(
                "publishing.reconciliation.published_recovered publish_job_id=%s",
                publish_job_id,
            )
            return PublishingReconciliationOutcome(publish_job, result.remote_state, True)
        if (
            result.remote_state is PublishingRemoteState.INCOMPLETE
            and checkpoint is not None
            and result.next_byte_offset is not None
            and result.next_byte_offset != checkpoint.next_byte_offset
        ):
            await self.upload_session_service.store(
                PublishingUploadSessionData(
                    publish_job_id=checkpoint.publish_job_id,
                    platform=checkpoint.platform,
                    session_uri=checkpoint.session_uri,
                    total_bytes=checkpoint.total_bytes,
                    next_byte_offset=result.next_byte_offset,
                )
            )
            logger.warning(
                "publishing.reconciliation.incomplete publish_job_id=%s "
                "remote_state=incomplete checkpoint_updated=true",
                publish_job_id,
            )
            return PublishingReconciliationOutcome(publish_job, result.remote_state, True)
        if result.remote_state is PublishingRemoteState.INCOMPLETE:
            logger.warning(
                "publishing.reconciliation.incomplete publish_job_id=%s "
                "remote_state=incomplete checkpoint_updated=false",
                publish_job_id,
            )
        elif result.remote_state is PublishingRemoteState.NOT_FOUND:
            logger.warning(
                "publishing.reconciliation.not_found publish_job_id=%s remote_state=not_found",
                publish_job_id,
            )
        elif result.remote_state is PublishingRemoteState.UNKNOWN:
            logger.info(
                "publishing.reconciliation.unknown publish_job_id=%s remote_state=unknown",
                publish_job_id,
            )
        return PublishingReconciliationOutcome(publish_job, result.remote_state, False)

    @staticmethod
    def _session(
        checkpoint: PublishingUploadSessionData | None,
    ) -> ResumablePublishingSession | None:
        if checkpoint is None:
            return None
        return ResumablePublishingSession(
            session_uri=checkpoint.session_uri,
            total_bytes=checkpoint.total_bytes,
            next_byte_offset=checkpoint.next_byte_offset,
        )

    @staticmethod
    def _apply_result(publish_job: PublishJob, result: PublishingResult) -> None:
        publish_job.remote_media_id = result.remote_media_id
        publish_job.remote_url = str(result.remote_url) if result.remote_url is not None else None
        publish_job.remote_status = result.remote_status
        publish_job.published_at = result.published_at
        publish_job.provider_metadata = PublishingReconciliationService._json_safe(
            result.provider_metadata
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value))
