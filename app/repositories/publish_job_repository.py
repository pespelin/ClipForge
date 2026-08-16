from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publish_job import PublishJob
from app.repositories.base import BaseRepository


class PublishJobRepository(BaseRepository[PublishJob]):
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
