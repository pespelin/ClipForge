from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import Script
from app.repositories.base import BaseRepository


class ScriptRepository(BaseRepository[Script]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, script: Script) -> Script:
        self.session.add(script)
        await self.session.flush()
        return script

    async def get(self, script_id: int) -> Script | None:
        result = await self.session.execute(select(Script).where(Script.id == script_id))
        return result.scalar_one_or_none()

    async def get_by_video_id(self, video_id: str) -> list[Script]:
        result = await self.session.execute(
            select(Script).where(Script.video_id == video_id).order_by(Script.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_analysis_id(self, video_analysis_id: int) -> list[Script]:
        result = await self.session.execute(
            select(Script)
            .where(Script.video_analysis_id == video_analysis_id)
            .order_by(Script.created_at.desc())
        )
        return list(result.scalars().all())

    async def save(self, script: Script) -> Script:
        await self.session.flush()
        return script

    async def commit(self) -> None:
        await self.session.commit()
