import asyncio

from sqlalchemy.exc import OperationalError

from app.core.exceptions import BrollRetrievalError
from app.db.session import AsyncSessionLocal
from app.providers.media import LocalMediaProvider
from app.repositories.broll_repository import BrollAssetRepository, BrollCollectionRepository
from app.repositories.script_repository import ScriptRepository
from app.services.broll_retrieval_service import BrollRetrievalService
from app.workers.celery_app import celery_app


async def _run_broll_retrieval(collection_id: int) -> dict[str, int | str]:
    async with AsyncSessionLocal() as session:
        service = BrollRetrievalService(
            script_repository=ScriptRepository(session),
            collection_repository=BrollCollectionRepository(session),
            asset_repository=BrollAssetRepository(session),
            media_provider=LocalMediaProvider(),
        )
        try:
            collection, assets = await service.process_collection(collection_id)
            await session.commit()
        except BrollRetrievalError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        return {
            "collection_id": collection.id,
            "collection_status": collection.status.value,
            "asset_count": len(assets),
        }


@celery_app.task(
    name="broll.retrieve",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def retrieve_broll(collection_id: int) -> dict[str, int | str]:
    """Compose B-roll dependencies and run async retrieval orchestration."""

    return asyncio.run(_run_broll_retrieval(collection_id))
