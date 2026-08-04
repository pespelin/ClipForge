import asyncio

from sqlalchemy.exc import OperationalError

from app.core.exceptions import ScriptGenerationError
from app.db.session import AsyncSessionLocal
from app.providers.script import LocalScriptGenerator
from app.repositories.script_repository import ScriptRepository
from app.repositories.video_analysis_repository import VideoAnalysisRepository
from app.repositories.video_repository import VideoRepository
from app.services.script_generation_service import ScriptGenerationService
from app.workers.celery_app import celery_app


async def _run_generation(script_id: int) -> dict[str, int | str]:
    async with AsyncSessionLocal() as session:
        service = ScriptGenerationService(
            video_repository=VideoRepository(session),
            analysis_repository=VideoAnalysisRepository(session),
            script_repository=ScriptRepository(session),
            generator=LocalScriptGenerator(),
        )
        try:
            script = await service.process_script(script_id)
            await session.commit()
        except ScriptGenerationError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {"script_id": script.id, "script_status": script.status.value}


@celery_app.task(
    name="scripts.generate",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def generate_script(script_id: int) -> dict[str, int | str]:
    """Compose script-generation dependencies and run async orchestration."""

    return asyncio.run(_run_generation(script_id))
