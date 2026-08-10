import asyncio

from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.exceptions import VideoRenderingError
from app.db.session import AsyncSessionLocal
from app.providers.render import FFmpegVideoRenderer
from app.repositories.broll_repository import BrollAssetRepository, BrollCollectionRepository
from app.repositories.script_repository import ScriptRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.repositories.voice_track_repository import VoiceTrackRepository
from app.services.video_render_service import VideoRenderService
from app.workers.celery_app import celery_app


async def _run_video_rendering(render_id: int) -> dict[str, int | str | None]:
    async with AsyncSessionLocal() as session:
        service = VideoRenderService(
            script_repository=ScriptRepository(session),
            voice_track_repository=VoiceTrackRepository(session),
            collection_repository=BrollCollectionRepository(session),
            asset_repository=BrollAssetRepository(session),
            render_repository=VideoRenderRepository(session),
            renderer=FFmpegVideoRenderer(get_settings().storage_root),
        )
        try:
            video_render = await service.process_render(render_id)
            await session.commit()
        except VideoRenderingError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {
            "render_id": video_render.id,
            "render_status": video_render.status.value,
            "storage_key": video_render.storage_key,
        }


@celery_app.task(
    name="video.render",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def render_video(render_id: int) -> dict[str, int | str | None]:
    """Compose video-rendering dependencies and run async orchestration."""

    return asyncio.run(_run_video_rendering(render_id))
