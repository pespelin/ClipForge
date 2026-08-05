from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broll import BrollAsset, BrollCollection
from app.repositories.base import BaseRepository


class BrollCollectionRepository(BaseRepository[BrollCollection]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, collection: BrollCollection) -> BrollCollection:
        self.session.add(collection)
        await self.session.flush()
        return collection

    async def get(self, collection_id: int) -> BrollCollection | None:
        result = await self.session.execute(
            select(BrollCollection).where(BrollCollection.id == collection_id)
        )
        return result.scalar_one_or_none()

    async def get_by_script_id(self, script_id: int) -> list[BrollCollection]:
        result = await self.session.execute(
            select(BrollCollection)
            .where(BrollCollection.script_id == script_id)
            .order_by(BrollCollection.created_at.desc(), BrollCollection.id.desc())
        )
        return list(result.scalars().all())

    async def save(self, collection: BrollCollection) -> BrollCollection:
        await self.session.flush()
        return collection

    async def commit(self) -> None:
        await self.session.commit()


class BrollAssetRepository(BaseRepository[BrollAsset]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, asset: BrollAsset) -> BrollAsset:
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def get(self, asset_id: int) -> BrollAsset | None:
        result = await self.session.execute(select(BrollAsset).where(BrollAsset.id == asset_id))
        return result.scalar_one_or_none()

    async def get_by_collection_id(self, collection_id: int) -> list[BrollAsset]:
        result = await self.session.execute(
            select(BrollAsset)
            .where(BrollAsset.collection_id == collection_id)
            .order_by(
                BrollAsset.script_section_order.asc().nulls_last(),
                BrollAsset.relevance_score.desc().nulls_last(),
                BrollAsset.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_by_section_order(
        self, collection_id: int, script_section_order: int
    ) -> list[BrollAsset]:
        result = await self.session.execute(
            select(BrollAsset)
            .where(
                BrollAsset.collection_id == collection_id,
                BrollAsset.script_section_order == script_section_order,
            )
            .order_by(
                BrollAsset.relevance_score.desc().nulls_last(),
                BrollAsset.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def save(self, asset: BrollAsset) -> BrollAsset:
        await self.session.flush()
        return asset

    async def commit(self) -> None:
        await self.session.commit()
