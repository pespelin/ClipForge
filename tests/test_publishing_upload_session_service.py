from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import (
    PublishingExecutionLeaseUnavailableError,
    PublishingUploadSessionNotFoundError,
)
from app.models.publish_job import PublishPlatform
from app.models.publishing_upload_session import PublishingUploadSession
from app.security import CredentialEncryptionError
from app.services.publishing_upload_session_service import (
    PublishingUploadSessionData,
    PublishingUploadSessionService,
)

SESSION_URI = "https://upload.youtube.test/secret-session-capability"


class FakeEncryptor:
    _PREFIX = "encrypted:"

    def encrypt(self, plaintext: str) -> str:
        return f"{self._PREFIX}{plaintext[::-1]}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(self._PREFIX):
            raise CredentialEncryptionError
        return ciphertext.removeprefix(self._PREFIX)[::-1]


class FakeRepository:
    def __init__(self, upload_session: PublishingUploadSession | None = None) -> None:
        self.upload_session = upload_session
        self.created = []
        self.saved = []
        self.deleted = []

    async def get_by_publish_job_id(self, publish_job_id: int):
        if self.upload_session and self.upload_session.publish_job_id == publish_job_id:
            return self.upload_session
        return None

    async def create(self, upload_session: PublishingUploadSession):
        self.upload_session = upload_session
        self.created.append(upload_session)
        return upload_session

    async def save(self, upload_session: PublishingUploadSession):
        self.saved.append(upload_session)
        return upload_session

    async def delete(self, upload_session: PublishingUploadSession):
        self.deleted.append(upload_session)
        self.upload_session = None


def data(**values) -> PublishingUploadSessionData:
    defaults = {
        "publish_job_id": 7,
        "platform": PublishPlatform.YOUTUBE,
        "session_uri": SESSION_URI,
        "total_bytes": 10,
        "next_byte_offset": 4,
    }
    defaults.update(values)
    return PublishingUploadSessionData(**defaults)


def make_service(repository: FakeRepository) -> PublishingUploadSessionService:
    return PublishingUploadSessionService(repository, FakeEncryptor())


async def test_store_encrypts_uri_before_creating_checkpoint() -> None:
    repository = FakeRepository()
    entity = await make_service(repository).store(data())

    assert entity.publish_job_id == 7
    assert entity.platform is PublishPlatform.YOUTUBE
    assert entity.encrypted_session_uri == f"encrypted:{SESSION_URI[::-1]}"
    assert entity.encrypted_session_uri != SESSION_URI
    assert entity.total_bytes == 10
    assert entity.next_byte_offset == 4
    assert repository.created == [entity]
    assert repository.saved == []


async def test_store_updates_existing_checkpoint_exactly() -> None:
    entity = PublishingUploadSession(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted:old",
        total_bytes=10,
        next_byte_offset=0,
    )
    repository = FakeRepository(entity)
    updated_uri = f"{SESSION_URI}/updated"

    result = await make_service(repository).store(
        data(session_uri=updated_uri, total_bytes=20, next_byte_offset=12)
    )

    assert result is entity
    assert entity.encrypted_session_uri == f"encrypted:{updated_uri[::-1]}"
    assert entity.total_bytes == 20
    assert entity.next_byte_offset == 12
    assert repository.saved == [entity]


async def test_get_decrypts_to_safe_internal_data_without_mutating_entity() -> None:
    ciphertext = f"encrypted:{SESSION_URI[::-1]}"
    entity = PublishingUploadSession(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri=ciphertext,
        total_bytes=10,
        next_byte_offset=4,
    )

    result = await make_service(FakeRepository(entity)).get_by_publish_job_id(7)

    assert result == data()
    assert result.session_uri == SESSION_URI
    assert SESSION_URI not in repr(result)
    assert entity.encrypted_session_uri == ciphertext


async def test_get_missing_checkpoint_returns_none() -> None:
    assert await make_service(FakeRepository()).get_by_publish_job_id(7) is None


async def test_delete_existing_and_missing_checkpoint() -> None:
    entity = PublishingUploadSession(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted:value",
        total_bytes=10,
    )
    repository = FakeRepository(entity)
    service = make_service(repository)

    assert await service.delete_by_publish_job_id(7) is True
    assert repository.deleted == [entity]
    assert await service.delete_by_publish_job_id(7) is False


async def test_encryption_failure_prevents_partial_persistence() -> None:
    class FailingEncryptor(FakeEncryptor):
        def encrypt(self, plaintext: str) -> str:
            raise CredentialEncryptionError

    repository = FakeRepository()
    service = PublishingUploadSessionService(repository, FailingEncryptor())

    with pytest.raises(CredentialEncryptionError) as error:
        await service.store(data())

    assert repository.created == []
    assert repository.saved == []
    assert SESSION_URI not in repr(error.value)


NOW = datetime(2030, 1, 1, tzinfo=UTC)


def leased_entity(
    *,
    owner: str | None = None,
    expires_at: datetime | None = None,
) -> PublishingUploadSession:
    return PublishingUploadSession(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted:value",
        total_bytes=10,
        execution_owner=owner,
        execution_lease_expires_at=expires_at,
    )


async def test_acquire_execution_lease_without_active_owner() -> None:
    entity = leased_entity()
    repository = FakeRepository(entity)

    result = await make_service(repository).acquire_execution_lease(
        7,
        owner="task-owner-a",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=900),
    )

    assert result is entity
    assert entity.execution_owner == "task-owner-a"
    assert entity.execution_lease_expires_at == NOW + timedelta(seconds=900)
    assert repository.saved == [entity]


async def test_same_owner_reacquire_updates_expiry() -> None:
    entity = leased_entity(owner="task-owner-a", expires_at=NOW + timedelta(seconds=100))
    repository = FakeRepository(entity)
    new_expiry = NOW + timedelta(seconds=900)

    await make_service(repository).acquire_execution_lease(
        7,
        owner="task-owner-a",
        now=NOW,
        lease_expires_at=new_expiry,
    )

    assert entity.execution_lease_expires_at == new_expiry
    assert repository.saved == [entity]


@pytest.mark.parametrize("current_expiry", [NOW, NOW - timedelta(seconds=1)])
async def test_expired_lease_allows_takeover(current_expiry: datetime) -> None:
    entity = leased_entity(owner="task-owner-a", expires_at=current_expiry)
    repository = FakeRepository(entity)

    await make_service(repository).acquire_execution_lease(
        7,
        owner="task-owner-b",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=900),
    )

    assert entity.execution_owner == "task-owner-b"
    assert repository.saved == [entity]


async def test_active_other_owner_lease_is_not_mutated() -> None:
    expiry = NOW + timedelta(seconds=60)
    entity = leased_entity(owner="task-owner-a", expires_at=expiry)
    repository = FakeRepository(entity)

    with pytest.raises(PublishingExecutionLeaseUnavailableError):
        await make_service(repository).acquire_execution_lease(
            7,
            owner="task-owner-b",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=900),
        )

    assert entity.execution_owner == "task-owner-a"
    assert entity.execution_lease_expires_at == expiry
    assert repository.saved == []


async def test_acquire_missing_checkpoint_is_controlled() -> None:
    with pytest.raises(PublishingUploadSessionNotFoundError):
        await make_service(FakeRepository()).acquire_execution_lease(
            7,
            owner="task-owner-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=900),
        )


async def test_release_only_matching_owner() -> None:
    entity = leased_entity(owner="task-owner-a", expires_at=NOW + timedelta(seconds=60))
    repository = FakeRepository(entity)
    service = make_service(repository)

    assert await service.release_execution_lease(7, owner="task-owner-b") is False
    assert entity.execution_owner == "task-owner-a"
    assert repository.saved == []
    assert await service.release_execution_lease(7, owner="task-owner-a") is True
    assert entity.execution_owner is None
    assert entity.execution_lease_expires_at is None
    assert repository.saved == [entity]


@pytest.mark.parametrize(
    ("now", "expiry"),
    [
        (datetime(2030, 1, 1), NOW + timedelta(seconds=1)),
        (NOW, datetime(2030, 1, 1, 0, 0, 1)),
        (NOW, NOW),
    ],
)
async def test_acquire_rejects_invalid_lease_timestamps(now: datetime, expiry: datetime) -> None:
    repository = FakeRepository(leased_entity())

    with pytest.raises(ValueError):
        await make_service(repository).acquire_execution_lease(
            7,
            owner="task-owner-a",
            now=now,
            lease_expires_at=expiry,
        )

    assert repository.saved == []
