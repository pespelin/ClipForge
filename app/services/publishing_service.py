import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

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
from app.providers.publishing import (
    PublishingProvider,
    ResumablePublishingProvider,
    ResumablePublishingSession,
)
from app.repositories.publish_job_repository import PublishJobRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.schemas.publish_job import (
    PublishingInput,
    PublishingResult,
    PublishMetadata,
    PublishOptions,
    PublishRequest,
)
from app.services.publishing_upload_session_service import (
    PublishingUploadSessionData,
    PublishingUploadSessionService,
)


@dataclass(frozen=True, slots=True)
class PublishingExecutionPlan:
    publish_job: PublishJob
    publishing_input: PublishingInput | None
    resumable_session: ResumablePublishingSession | None
    requires_checkpoint_commit: bool
    already_complete: bool = False


class PublishingService:
    def __init__(
        self,
        video_render_repository: VideoRenderRepository,
        publish_job_repository: PublishJobRepository,
        publishing_provider: PublishingProvider,
        upload_session_service: PublishingUploadSessionService | None = None,
    ) -> None:
        self.video_render_repository = video_render_repository
        self.publish_job_repository = publish_job_repository
        self.publishing_provider = publishing_provider
        self.upload_session_service = upload_session_service

    async def create_publish_job(self, video_render_id: int, request: PublishRequest) -> PublishJob:
        validated_request = PublishRequest.model_validate(request)
        video_render = await self._get_video_render(video_render_id)
        self._verify_render_ready(video_render)
        metadata = PublishMetadata.model_validate(validated_request.metadata)
        options = PublishOptions.model_validate(validated_request.options)
        options_snapshot = self._json_safe(options.model_dump(mode="json"))

        return await self.publish_job_repository.create(
            PublishJob(
                video_render_id=video_render.id,
                status=PublishStatus.PENDING,
                platform=options.platform,
                account_reference=options.account_reference,
                title=metadata.title,
                description=metadata.description,
                tags=list(metadata.tags),
                category=metadata.category,
                visibility=options.visibility,
                made_for_kids=options.made_for_kids,
                notify_subscribers=options.notify_subscribers,
                language=options.language,
                recording_date=metadata.recording_date,
                scheduled_publish_at=options.scheduled_publish_at,
                publish_options=options_snapshot,
                source_storage_key=video_render.storage_key,
                source_checksum=video_render.checksum,
                source_file_size_bytes=video_render.file_size_bytes,
                source_duration_seconds=video_render.duration_seconds,
                provider_metadata={},
            )
        )

    async def request_publish_job(
        self, video_render_id: int, request: PublishRequest
    ) -> tuple[PublishJob, bool]:
        publish_job = await self.create_publish_job(video_render_id, request)
        return publish_job, self._is_due(publish_job)

    async def prepare_publish_retry(self, publish_job_id: int) -> tuple[PublishJob, bool]:
        publish_job = await self.get_publish_job(publish_job_id)
        if publish_job.status in {PublishStatus.PUBLISHED, PublishStatus.PUBLISHING}:
            return publish_job, False
        if publish_job.status == PublishStatus.CANCELLED:
            raise PublishJobCancelledError

        if publish_job.status == PublishStatus.FAILED:
            publish_job.status = PublishStatus.PENDING
            publish_job.completed_at = None
            publish_job.error_message = None
            await self.publish_job_repository.save(publish_job)
        return publish_job, self._is_due(publish_job)

    async def mark_publish_enqueue_failed(self, publish_job: PublishJob, error: Exception) -> None:
        publish_job.status = PublishStatus.FAILED
        publish_job.completed_at = None
        message = str(error).strip() or type(error).__name__
        publish_job.error_message = f"Publishing task enqueue failed: {message}"
        await self.publish_job_repository.save(publish_job)

    async def process_publish_job(self, publish_job_id: int) -> PublishJob:
        plan = await self.prepare_publish_job_execution(publish_job_id)
        if plan.requires_checkpoint_commit:
            raise RuntimeError("Resumable publishing requires an explicit checkpoint commit")
        return await self.execute_prepared_publish(plan)

    async def prepare_publish_job_execution(self, publish_job_id: int) -> PublishingExecutionPlan:
        publish_job = await self.get_publish_job(publish_job_id)
        if publish_job.status == PublishStatus.PUBLISHED:
            return PublishingExecutionPlan(publish_job, None, None, False, True)
        if publish_job.status == PublishStatus.CANCELLED:
            raise PublishJobCancelledError
        self._verify_due(publish_job)

        publish_job.status = PublishStatus.PUBLISHING
        publish_job.completed_at = None
        publish_job.error_message = None
        await self.publish_job_repository.save(publish_job)

        try:
            publishing_input = self._build_publishing_input(publish_job)
            if not isinstance(self.publishing_provider, ResumablePublishingProvider):
                return PublishingExecutionPlan(publish_job, publishing_input, None, False)
            if self.upload_session_service is None:
                raise RuntimeError("Resumable upload persistence is not composed")
            persisted = await self.upload_session_service.get_by_publish_job_id(publish_job.id)
            if persisted is not None:
                if persisted.platform != publish_job.platform:
                    raise ValueError("Persisted upload session platform mismatch")
                return PublishingExecutionPlan(
                    publish_job,
                    publishing_input,
                    ResumablePublishingSession(
                        session_uri=persisted.session_uri,
                        total_bytes=persisted.total_bytes,
                        next_byte_offset=persisted.next_byte_offset,
                    ),
                    False,
                )

            session = await self.publishing_provider.initiate_upload(publishing_input)
            await self.upload_session_service.store(
                PublishingUploadSessionData(
                    publish_job_id=publish_job.id,
                    platform=publish_job.platform,
                    session_uri=session.session_uri,
                    total_bytes=session.total_bytes,
                    next_byte_offset=session.next_byte_offset,
                )
            )
            return PublishingExecutionPlan(publish_job, publishing_input, session, True)
        except Exception as error:
            await self._mark_failed(publish_job, error)
            if isinstance(error, PublishingError):
                raise
            raise PublishingError from error

    async def execute_prepared_publish(self, plan: PublishingExecutionPlan) -> PublishJob:
        if plan.already_complete:
            return plan.publish_job
        publish_job = plan.publish_job
        if plan.publishing_input is None:
            raise PublishingError
        try:
            if plan.resumable_session is None:
                raw_result = await self.publishing_provider.publish(plan.publishing_input)
            else:
                if not isinstance(self.publishing_provider, ResumablePublishingProvider):
                    raise RuntimeError("Prepared resumable provider capability is unavailable")
                raw_result = await self.publishing_provider.resume_upload(
                    plan.publishing_input, plan.resumable_session
                )
            result = PublishingResult.model_validate(raw_result)
            self._apply_result(publish_job, result)
            publish_job.status = PublishStatus.PUBLISHED
            publish_job.completed_at = datetime.now(UTC)
            publish_job.error_message = None
            if plan.resumable_session is not None:
                if self.upload_session_service is None:
                    raise RuntimeError("Resumable upload persistence is not composed")
                await self.upload_session_service.delete_by_publish_job_id(publish_job.id)
            return await self.publish_job_repository.save(publish_job)
        except Exception as error:
            await self._mark_failed(publish_job, error)
            if isinstance(error, PublishingError):
                raise
            raise PublishingError from error

    async def _mark_failed(self, publish_job: PublishJob, error: Exception) -> None:
        publish_job.status = PublishStatus.FAILED
        publish_job.completed_at = None
        publish_job.error_message = self._error_message(error)
        await self.publish_job_repository.save(publish_job)

    async def get_publish_job(self, publish_job_id: int) -> PublishJob:
        publish_job = await self.publish_job_repository.get(publish_job_id)
        if publish_job is None:
            raise PublishJobNotFoundError
        return publish_job

    async def list_publish_jobs_for_render(self, video_render_id: int) -> list[PublishJob]:
        await self._get_video_render(video_render_id)
        return await self.publish_job_repository.get_by_video_render_id(video_render_id)

    async def cancel_publish_job(self, publish_job_id: int) -> PublishJob:
        publish_job = await self.get_publish_job(publish_job_id)
        if publish_job.status == PublishStatus.CANCELLED:
            return publish_job
        if publish_job.status in {PublishStatus.PUBLISHING, PublishStatus.PUBLISHED}:
            raise PublishCancellationConflictError

        publish_job.status = PublishStatus.CANCELLED
        publish_job.completed_at = datetime.now(UTC)
        publish_job.error_message = None
        return await self.publish_job_repository.save(publish_job)

    async def _get_video_render(self, video_render_id: int) -> VideoRender:
        video_render = await self.video_render_repository.get(video_render_id)
        if video_render is None:
            raise VideoRenderNotFoundError
        return video_render

    @staticmethod
    def _verify_render_ready(video_render: VideoRender) -> None:
        if video_render.status != VideoRenderStatus.COMPLETED:
            raise RenderNotReadyForPublishingError
        if (
            video_render.storage_key is None
            or not video_render.storage_key.strip()
            or video_render.duration_seconds is None
            or video_render.duration_seconds <= 0
            or video_render.file_size_bytes is None
            or video_render.file_size_bytes < 0
            or (video_render.checksum is not None and not video_render.checksum.strip())
        ):
            raise UnusablePublishArtifactError

    @staticmethod
    def _verify_due(publish_job: PublishJob) -> None:
        scheduled = publish_job.scheduled_publish_at
        if scheduled is None:
            return
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            raise PublishNotDueError
        if scheduled > datetime.now(UTC):
            raise PublishNotDueError

    @staticmethod
    def _is_due(publish_job: PublishJob) -> bool:
        try:
            PublishingService._verify_due(publish_job)
        except PublishNotDueError:
            return False
        return True

    @staticmethod
    def _build_publishing_input(publish_job: PublishJob) -> PublishingInput:
        try:
            options = PublishOptions.model_validate(publish_job.publish_options)
            return PublishingInput(
                publish_job_id=publish_job.id,
                video_render_id=publish_job.video_render_id,
                platform=publish_job.platform,
                account_reference=publish_job.account_reference,
                source_storage_key=publish_job.source_storage_key,
                source_checksum=publish_job.source_checksum,
                source_file_size_bytes=publish_job.source_file_size_bytes,
                source_duration_seconds=publish_job.source_duration_seconds,
                title=publish_job.title,
                description=publish_job.description,
                tags=publish_job.tags,
                category=publish_job.category,
                visibility=publish_job.visibility,
                made_for_kids=publish_job.made_for_kids,
                notify_subscribers=publish_job.notify_subscribers,
                language=publish_job.language,
                recording_date=publish_job.recording_date,
                scheduled_publish_at=publish_job.scheduled_publish_at,
                publish_options=options,
            )
        except ValidationError as error:
            raise ValueError("Persisted publish job contains invalid structured input") from error

    @staticmethod
    def _apply_result(publish_job: PublishJob, result: PublishingResult) -> None:
        publish_job.remote_media_id = result.remote_media_id
        publish_job.remote_url = str(result.remote_url) if result.remote_url is not None else None
        publish_job.remote_status = result.remote_status
        publish_job.published_at = result.published_at
        publish_job.provider_metadata = PublishingService._json_safe(result.provider_metadata)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value))

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "Publishing provider returned an invalid structured result"
        return str(error).strip() or type(error).__name__
