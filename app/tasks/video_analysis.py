import asyncio

from sqlalchemy.exc import OperationalError

from app.core.exceptions import AnalysisProcessingError
from app.db.session import AsyncSessionLocal
from app.providers.analysis import LocalVideoAnalyzer
from app.repositories.video_analysis_repository import VideoAnalysisRepository
from app.repositories.video_repository import VideoRepository
from app.services.video_analysis_service import VideoAnalysisService
from app.workers.celery_app import celery_app


async def _run_analysis(video_id: str) -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        service = VideoAnalysisService(
            video_repository=VideoRepository(session),
            analysis_repository=VideoAnalysisRepository(session),
            analyzer=LocalVideoAnalyzer(),
        )
        try:
            analysis = await service.process_analysis(video_id)
            await session.commit()
        except AnalysisProcessingError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {"video_id": video_id, "analysis_status": analysis.status.value}


@celery_app.task(
    name="videos.analyze",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def analyze_video(video_id: str) -> dict[str, str]:
    """Compose analysis dependencies and run async orchestration in a worker."""

    return asyncio.run(_run_analysis(video_id))
