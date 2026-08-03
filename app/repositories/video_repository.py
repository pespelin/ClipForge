from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.video import Transcript, Video
from app.repositories.base import BaseRepository


class VideoRepository(BaseRepository[Video]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, video: Video) -> Video:
        self.session.add(video)
        await self.session.flush()
        return video

    async def get(self, video_id: str) -> Video | None:
        result = await self.session.execute(
            select(Video).options(selectinload(Video.transcript)).where(Video.id == video_id)
        )
        return result.scalar_one_or_none()

    async def update_status(self, video: Video, status: str) -> None:
        video.status = status
        await self.session.flush()

    async def upsert_transcript(
        self, video: Video, language: str | None, full_text: str
    ) -> Transcript:
        transcript = video.transcript
        if transcript is None:
            transcript = Transcript(video=video, language=language, full_text=full_text)
            self.session.add(transcript)
        else:
            transcript.language = language
            transcript.full_text = full_text
        await self.session.flush()
        return transcript
