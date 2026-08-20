from dataclasses import dataclass, field

from app.models.publish_job import PublishPlatform
from app.models.publishing_upload_session import PublishingUploadSession
from app.repositories.publishing_upload_session_repository import (
    PublishingUploadSessionRepository,
)
from app.security import CredentialEncryptor


@dataclass(frozen=True, slots=True)
class PublishingUploadSessionData:
    publish_job_id: int
    platform: PublishPlatform
    session_uri: str = field(repr=False)
    total_bytes: int
    next_byte_offset: int = 0


class PublishingUploadSessionService:
    def __init__(
        self,
        repository: PublishingUploadSessionRepository,
        encryptor: CredentialEncryptor,
    ) -> None:
        self.repository = repository
        self.encryptor = encryptor

    async def store(self, session_data: PublishingUploadSessionData) -> PublishingUploadSession:
        encrypted_session_uri = self.encryptor.encrypt(session_data.session_uri)
        upload_session = await self.repository.get_by_publish_job_id(session_data.publish_job_id)
        if upload_session is None:
            return await self.repository.create(
                PublishingUploadSession(
                    publish_job_id=session_data.publish_job_id,
                    platform=session_data.platform,
                    encrypted_session_uri=encrypted_session_uri,
                    total_bytes=session_data.total_bytes,
                    next_byte_offset=session_data.next_byte_offset,
                )
            )

        upload_session.platform = session_data.platform
        upload_session.encrypted_session_uri = encrypted_session_uri
        upload_session.total_bytes = session_data.total_bytes
        upload_session.next_byte_offset = session_data.next_byte_offset
        return await self.repository.save(upload_session)

    async def get_by_publish_job_id(
        self, publish_job_id: int
    ) -> PublishingUploadSessionData | None:
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        if upload_session is None:
            return None
        return PublishingUploadSessionData(
            publish_job_id=upload_session.publish_job_id,
            platform=upload_session.platform,
            session_uri=self.encryptor.decrypt(upload_session.encrypted_session_uri),
            total_bytes=upload_session.total_bytes,
            next_byte_offset=upload_session.next_byte_offset,
        )

    async def delete_by_publish_job_id(self, publish_job_id: int) -> bool:
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        if upload_session is None:
            return False
        await self.repository.delete(upload_session)
        return True
