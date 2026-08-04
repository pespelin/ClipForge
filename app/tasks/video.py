import asyncio

from app.api.dependencies import get_video_service
from app.db.session import AsyncSessionLocal
from app.tasks.video_analysis import analyze_video
from app.workers.celery_app import celery_app


@celery_app.task(
    name="videos.process",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def process_video(self, video_id: str) -> None:
    """Celery entrypoint; all pipeline logic remains in VideoService."""

    async def run() -> None:
        async with AsyncSessionLocal() as session:
            service = get_video_service(session)
            await service.process(video_id)

    asyncio.run(run())
    analyze_video.delay(video_id)
