from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publishing_upload_session import PublishingUploadSession
from app.repositories.base import BaseRepository


class PublishingUploadSessionRepository(BaseRepository[PublishingUploadSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, upload_session: PublishingUploadSession) -> PublishingUploadSession:
        self.session.add(upload_session)
        await self.session.flush()
        return upload_session

    async def save(self, upload_session: PublishingUploadSession) -> PublishingUploadSession:
        await self.session.flush()
        return upload_session

    async def get_by_id(self, upload_session_id: int) -> PublishingUploadSession | None:
        result = await self.session.execute(
            select(PublishingUploadSession).where(PublishingUploadSession.id == upload_session_id)
        )
        return result.scalar_one_or_none()

    async def get_by_publish_job_id(self, publish_job_id: int) -> PublishingUploadSession | None:
        result = await self.session.execute(
            select(PublishingUploadSession).where(
                PublishingUploadSession.publish_job_id == publish_job_id
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, upload_session: PublishingUploadSession) -> None:
        await self.session.delete(upload_session)
        await self.session.flush()
