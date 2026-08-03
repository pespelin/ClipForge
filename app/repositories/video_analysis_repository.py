from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video_analysis import VideoAnalysis
from app.repositories.base import BaseRepository


class VideoAnalysisRepository(BaseRepository[VideoAnalysis]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, analysis: VideoAnalysis) -> VideoAnalysis:
        self.session.add(analysis)
        await self.session.flush()
        return analysis

    async def get(self, analysis_id: int) -> VideoAnalysis | None:
        result = await self.session.execute(
            select(VideoAnalysis).where(VideoAnalysis.id == analysis_id)
        )
        return result.scalar_one_or_none()

    async def get_by_video_id(self, video_id: str) -> VideoAnalysis | None:
        result = await self.session.execute(
            select(VideoAnalysis).where(VideoAnalysis.video_id == video_id)
        )
        return result.scalar_one_or_none()

    async def save(self, analysis: VideoAnalysis) -> VideoAnalysis:
        await self.session.flush()
        return analysis
