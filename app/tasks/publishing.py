import asyncio
import logging
from contextlib import AsyncExitStack

import httpx
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.exceptions import (
    PublishingError,
    PublishingExecutionLeaseUnavailableError,
    PublishingExecutionLockUnavailableError,
    PublishingExecutionOwnerUnavailableError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.core.observability import publishing_failure_category
from app.db.session import AsyncSessionLocal
from app.providers.publishing import create_publishing_composition
from app.repositories.publish_job_repository import PublishJobRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.services.publishing_service import PublishingService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_publishing(
    publish_job_id: int,
    execution_owner: str | None = None,
) -> dict[str, int | str | None]:
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
            execution_owner=execution_owner,
            execution_lease_seconds=settings.publishing_execution_lease_seconds,
        )
        try:
            plan = await service.prepare_publish_job_execution(publish_job_id)
            if plan.requires_pre_execution_commit:
                await session.commit()
                if getattr(plan, "checkpoint_created", False):
                    logger.info(
                        "publishing.execution.checkpoint_created publish_job_id=%s",
                        publish_job_id,
                    )
                else:
                    logger.info(
                        "publishing.execution.resumed publish_job_id=%s",
                        publish_job_id,
                    )
            elif getattr(plan, "resumable_session", None) is not None:
                logger.info(
                    "publishing.execution.resumed publish_job_id=%s",
                    publish_job_id,
                )
            publish_job = await service.execute_prepared_publish(plan)
            await session.commit()
        except (
            PublishingExecutionLeaseUnavailableError,
            PublishingExecutionLockUnavailableError,
            PublishingExecutionOwnerUnavailableError,
        ):
            await session.rollback()
            raise
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
    logger.info("publishing.execution.started publish_job_id=%s", publish_job_id)
    try:
        result = asyncio.run(
            _run_publishing(
                publish_job_id,
                execution_owner=self.request.id,
            )
        )
        logger.info(
            "publishing.execution.succeeded publish_job_id=%s status=%s",
            publish_job_id,
            result["publish_status"],
        )
        return result
    except PublishingExecutionLeaseUnavailableError as error:
        logger.warning(
            "publishing.execution.retry_scheduled publish_job_id=%s "
            "failure_category=lease_contention retry_after_seconds=5",
            publish_job_id,
        )
        raise self.retry(exc=error, countdown=5) from error
    except PublishingExecutionLockUnavailableError as error:
        logger.warning(
            "publishing.execution.retry_scheduled publish_job_id=%s "
            "failure_category=lock_contention retry_after_seconds=5",
            publish_job_id,
        )
        raise self.retry(exc=error, countdown=5) from error
    except PublishingRateLimitError as error:
        countdown = error.retry_after_seconds
        retry_countdown = countdown if countdown is not None else 5
        logger.warning(
            "publishing.execution.retry_scheduled publish_job_id=%s "
            "failure_category=rate_limit retry_after_seconds=%s",
            publish_job_id,
            retry_countdown,
        )
        raise self.retry(
            exc=error,
            countdown=retry_countdown,
        ) from error
    except PublishingTransientError as error:
        logger.warning(
            "publishing.execution.retry_scheduled publish_job_id=%s "
            "failure_category=transient retry_after_seconds=5",
            publish_job_id,
        )
        raise self.retry(exc=error, countdown=5) from error
    except OperationalError:
        logger.warning(
            "publishing.execution.retry_scheduled publish_job_id=%s failure_category=database",
            publish_job_id,
        )
        raise
    except PublishingError as error:
        logger.error(
            "publishing.execution.failed publish_job_id=%s failure_category=%s",
            publish_job_id,
            publishing_failure_category(error),
        )
        raise
    except Exception:
        logger.error(
            "publishing.execution.failed publish_job_id=%s failure_category=unexpected",
            publish_job_id,
        )
        raise
