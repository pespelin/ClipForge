from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PublishingExecutionLockUnavailableError
from app.models.publish_job import PublishJob
from app.repositories.base import BaseRepository


class PublishJobRepository(BaseRepository[PublishJob]):
    _LOCK_TIMEOUT_SQLSTATE = "55P03"
    _LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '5s'")

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, publish_job: PublishJob) -> PublishJob:
        self.session.add(publish_job)
        await self.session.flush()
        return publish_job

    async def get(self, publish_job_id: int) -> PublishJob | None:
        result = await self.session.execute(
            select(PublishJob).where(PublishJob.id == publish_job_id)
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, publish_job_id: int) -> PublishJob | None:
        try:
            await self.session.execute(self._LOCK_TIMEOUT)
            result = await self.session.execute(
                select(PublishJob).where(PublishJob.id == publish_job_id).with_for_update()
            )
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == self._LOCK_TIMEOUT_SQLSTATE:
                raise PublishingExecutionLockUnavailableError from None
            raise
        return result.scalar_one_or_none()

    async def get_by_video_render_id(self, video_render_id: int) -> list[PublishJob]:
        result = await self.session.execute(
            select(PublishJob)
            .where(PublishJob.video_render_id == video_render_id)
            .order_by(PublishJob.created_at.desc(), PublishJob.id.desc())
        )
        return list(result.scalars().all())

    async def save(self, publish_job: PublishJob) -> PublishJob:
        await self.session.flush()
        return publish_job
