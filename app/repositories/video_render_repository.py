from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video_render import VideoRender
from app.repositories.base import BaseRepository


class VideoRenderRepository(BaseRepository[VideoRender]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, video_render: VideoRender) -> VideoRender:
        self.session.add(video_render)
        await self.session.flush()
        return video_render

    async def get(self, render_id: int) -> VideoRender | None:
        result = await self.session.execute(select(VideoRender).where(VideoRender.id == render_id))
        return result.scalar_one_or_none()

    async def get_by_script_id(self, script_id: int) -> list[VideoRender]:
        result = await self.session.execute(
            select(VideoRender)
            .where(VideoRender.script_id == script_id)
            .order_by(VideoRender.created_at.desc(), VideoRender.id.desc())
        )
        return list(result.scalars().all())

    async def get_by_voice_track_id(self, voice_track_id: int) -> list[VideoRender]:
        result = await self.session.execute(
            select(VideoRender)
            .where(VideoRender.voice_track_id == voice_track_id)
            .order_by(VideoRender.created_at.desc(), VideoRender.id.desc())
        )
        return list(result.scalars().all())

    async def save(self, video_render: VideoRender) -> VideoRender:
        await self.session.flush()
        return video_render

    async def commit(self) -> None:
        await self.session.commit()
