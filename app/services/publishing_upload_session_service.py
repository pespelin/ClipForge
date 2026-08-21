from dataclasses import dataclass, field
from datetime import datetime

from app.core.exceptions import (
    PublishingExecutionLeaseLostError,
    PublishingExecutionLeaseUnavailableError,
    PublishingUploadSessionNotFoundError,
)
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

    async def acquire_execution_lease(
        self,
        publish_job_id: int,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublishingUploadSession:
        self._validate_lease_values(owner, now, lease_expires_at)
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        if upload_session is None:
            raise PublishingUploadSessionNotFoundError

        active_other_owner = (
            upload_session.execution_owner is not None
            and upload_session.execution_owner != owner
            and upload_session.execution_lease_expires_at is not None
            and upload_session.execution_lease_expires_at > now
        )
        if active_other_owner:
            raise PublishingExecutionLeaseUnavailableError

        upload_session.execution_owner = owner
        upload_session.execution_lease_expires_at = lease_expires_at
        return await self.repository.save(upload_session)

    async def release_execution_lease(self, publish_job_id: int, *, owner: str) -> bool:
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        if upload_session is None or upload_session.execution_owner != owner:
            return False
        upload_session.execution_owner = None
        upload_session.execution_lease_expires_at = None
        await self.repository.save(upload_session)
        return True

    async def renew_execution_lease(
        self,
        publish_job_id: int,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublishingUploadSession:
        self._validate_lease_values(owner, now, lease_expires_at)
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        if upload_session is None:
            raise PublishingUploadSessionNotFoundError
        if (
            upload_session.execution_owner != owner
            or upload_session.execution_lease_expires_at is None
            or upload_session.execution_lease_expires_at <= now
        ):
            raise PublishingExecutionLeaseLostError

        upload_session.execution_lease_expires_at = lease_expires_at
        return await self.repository.save(upload_session)

    async def is_execution_lease_active(
        self,
        publish_job_id: int,
        *,
        now: datetime,
    ) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Execution lease clock must be timezone-aware")
        upload_session = await self.repository.get_by_publish_job_id(publish_job_id)
        return bool(
            upload_session is not None
            and upload_session.execution_owner is not None
            and upload_session.execution_lease_expires_at is not None
            and upload_session.execution_lease_expires_at > now
        )

    @staticmethod
    def _validate_lease_values(
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        if not owner or len(owner) > 255:
            raise ValueError("Execution owner is invalid")
        if (
            now.tzinfo is None
            or now.utcoffset() is None
            or lease_expires_at.tzinfo is None
            or lease_expires_at.utcoffset() is None
            or lease_expires_at <= now
        ):
            raise ValueError("Execution lease timestamps are invalid")
