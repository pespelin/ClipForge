from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice_track import VoiceTrack
from app.repositories.base import BaseRepository


class VoiceTrackRepository(BaseRepository[VoiceTrack]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, voice_track: VoiceTrack) -> VoiceTrack:
        self.session.add(voice_track)
        await self.session.flush()
        return voice_track

    async def get(self, voice_track_id: int) -> VoiceTrack | None:
        result = await self.session.execute(
            select(VoiceTrack).where(VoiceTrack.id == voice_track_id)
        )
        return result.scalar_one_or_none()

    async def get_by_script_id(self, script_id: int) -> list[VoiceTrack]:
        result = await self.session.execute(
            select(VoiceTrack)
            .where(VoiceTrack.script_id == script_id)
            .order_by(VoiceTrack.created_at.desc())
        )
        return list(result.scalars().all())

    async def save(self, voice_track: VoiceTrack) -> VoiceTrack:
        await self.session.flush()
        return voice_track

    async def commit(self) -> None:
        await self.session.commit()
