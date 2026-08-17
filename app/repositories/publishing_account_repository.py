from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publish_job import PublishPlatform
from app.models.publishing_account import PublishingAccount
from app.repositories.base import BaseRepository


class PublishingAccountRepository(BaseRepository[PublishingAccount]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, account: PublishingAccount) -> PublishingAccount:
        self.session.add(account)
        await self.session.flush()
        return account

    async def save(self, account: PublishingAccount) -> PublishingAccount:
        await self.session.flush()
        return account

    async def get_by_id(self, account_id: int) -> PublishingAccount | None:
        result = await self.session.execute(
            select(PublishingAccount).where(PublishingAccount.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_by_platform_and_remote_account_id(
        self, platform: PublishPlatform, remote_account_id: str
    ) -> PublishingAccount | None:
        result = await self.session.execute(
            select(PublishingAccount).where(
                PublishingAccount.platform == platform,
                PublishingAccount.remote_account_id == remote_account_id,
            )
        )
        return result.scalar_one_or_none()
