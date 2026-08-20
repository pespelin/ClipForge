import asyncio

from sqlalchemy.exc import OperationalError

from app.core.exceptions import PublishingError
from app.db.session import AsyncSessionLocal
from app.providers.publishing import create_publishing_provider
from app.repositories.publish_job_repository import PublishJobRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.services.publishing_service import PublishingService
from app.workers.celery_app import celery_app


async def _run_publishing(publish_job_id: int) -> dict[str, int | str | None]:
    async with AsyncSessionLocal() as session:
        service = PublishingService(
            video_render_repository=VideoRenderRepository(session),
            publish_job_repository=PublishJobRepository(session),
            publishing_provider=create_publishing_provider(),
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
    name="publish.execute",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def execute_publish(publish_job_id: int) -> dict[str, int | str | None]:
    """Compose publishing dependencies and run async orchestration."""

    return asyncio.run(_run_publishing(publish_job_id))
