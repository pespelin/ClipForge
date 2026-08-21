import asyncio
from contextlib import AsyncExitStack

import httpx
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.exceptions import (
    PublishingError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.db.session import AsyncSessionLocal
from app.providers.publishing import create_publishing_composition
from app.repositories.publish_job_repository import PublishJobRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.services.publishing_service import PublishingService
from app.workers.celery_app import celery_app


async def _run_publishing(publish_job_id: int) -> dict[str, int | str | None]:
    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(AsyncSessionLocal())
        settings = get_settings()
        http_client = (
            await stack.enter_async_context(httpx.AsyncClient(timeout=30.0))
            if settings.publishing_provider == "youtube"
            else None
        )
        composition = create_publishing_composition(
            settings=settings,
            session=session if http_client is not None else None,
            http_client=http_client,
        )
        service = PublishingService(
            video_render_repository=VideoRenderRepository(session),
            publish_job_repository=PublishJobRepository(session),
            publishing_provider=composition.provider,
            upload_session_service=composition.upload_session_service,
        )
        try:
            plan = await service.prepare_publish_job_execution(publish_job_id)
            if plan.requires_checkpoint_commit:
                await session.commit()
            publish_job = await service.execute_prepared_publish(plan)
            await session.commit()
        except PublishingError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {
            "publish_job_id": publish_job.id,
            "publish_status": publish_job.status.value,
            "remote_media_id": publish_job.remote_media_id,
        }


@celery_app.task(
    bind=True,
    name="publish.execute",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def execute_publish(self, publish_job_id: int) -> dict[str, int | str | None]:
    """Compose publishing dependencies and run async orchestration."""
    try:
        return asyncio.run(_run_publishing(publish_job_id))
    except PublishingRateLimitError as error:
        countdown = error.retry_after_seconds
        raise self.retry(
            exc=error,
            countdown=countdown if countdown is not None else 5,
        ) from error
    except PublishingTransientError as error:
        raise self.retry(exc=error, countdown=5) from error
