import asyncio

from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.exceptions import VoiceGenerationError
from app.db.session import AsyncSessionLocal
from app.providers.tts import LocalTTSProvider
from app.repositories.script_repository import ScriptRepository
from app.repositories.voice_track_repository import VoiceTrackRepository
from app.services.voice_generation_service import VoiceGenerationService
from app.workers.celery_app import celery_app


async def _run_voice_generation(voice_track_id: int) -> dict[str, int | str | None]:
    async with AsyncSessionLocal() as session:
        service = VoiceGenerationService(
            script_repository=ScriptRepository(session),
            voice_track_repository=VoiceTrackRepository(session),
            tts_provider=LocalTTSProvider(get_settings().storage_root),
        )
        try:
            voice_track = await service.process_voice_track(voice_track_id)
            await session.commit()
        except VoiceGenerationError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {
            "voice_track_id": voice_track.id,
            "voice_track_status": voice_track.status.value,
            "storage_key": voice_track.storage_key,
        }


@celery_app.task(
    name="voice.generate",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def generate_voice(voice_track_id: int) -> dict[str, int | str | None]:
    """Compose voice-generation dependencies and run async orchestration."""

    return asyncio.run(_run_voice_generation(voice_track_id))
