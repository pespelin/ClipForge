from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_credential import OAuthCredential
from app.repositories.base import BaseRepository


class OAuthCredentialRepository(BaseRepository[OAuthCredential]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, credential: OAuthCredential) -> OAuthCredential:
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def save(self, credential: OAuthCredential) -> OAuthCredential:
        await self.session.flush()
        return credential

    async def get_by_id(self, credential_id: int) -> OAuthCredential | None:
        result = await self.session.execute(
            select(OAuthCredential).where(OAuthCredential.id == credential_id)
        )
        return result.scalar_one_or_none()

    async def get_by_publishing_account_id(
        self, publishing_account_id: int
    ) -> OAuthCredential | None:
        result = await self.session.execute(
            select(OAuthCredential).where(
                OAuthCredential.publishing_account_id == publishing_account_id
            )
        )
        return result.scalar_one_or_none()
