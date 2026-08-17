from datetime import UTC, datetime

import pytest

from app.models.oauth_credential import OAuthCredential
from app.security import CredentialEncryptionError, CredentialEncryptor
from app.services.oauth_credential_service import (
    OAuthCredentialInput,
    OAuthCredentialService,
)

ACCESS_TOKEN = "clipforge-test-access-token-never-real"
REFRESH_TOKEN = "clipforge-test-refresh-token-never-real"


class FakeEncryptor:
    _PREFIX = "fake-encrypted:"

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on

    def encrypt(self, plaintext: str) -> str:
        if plaintext == self.fail_on:
            raise CredentialEncryptionError
        return f"{self._PREFIX}{plaintext[::-1]}"

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext == self.fail_on or not ciphertext.startswith(self._PREFIX):
            raise CredentialEncryptionError
        return ciphertext.removeprefix(self._PREFIX)[::-1]


class FakeRepository:
    def __init__(self, credential: OAuthCredential | None = None) -> None:
        self.credential = credential
        self.created: list[OAuthCredential] = []
        self.saved: list[OAuthCredential] = []
        self.get_calls = 0

    async def get_by_publishing_account_id(self, publishing_account_id: int):
        self.get_calls += 1
        if (
            self.credential is not None
            and self.credential.publishing_account_id == publishing_account_id
        ):
            return self.credential
        return None

    async def create(self, credential: OAuthCredential):
        self.credential = credential
        self.created.append(credential)
        return credential

    async def save(self, credential: OAuthCredential):
        self.credential = credential
        self.saved.append(credential)
        return credential


def make_service(repository: FakeRepository, encryptor: CredentialEncryptor | None = None):
    return OAuthCredentialService(repository, encryptor or FakeEncryptor())


async def test_store_encrypts_plaintext_before_creating_entity() -> None:
    repository = FakeRepository()
    service = make_service(repository)
    expires_at = datetime(2030, 1, 1, tzinfo=UTC)

    credential = await service.store(
        7,
        OAuthCredentialInput(
            access_token=ACCESS_TOKEN,
            refresh_token=REFRESH_TOKEN,
            token_type="Bearer",
            scope="test.read test.write",
            expires_at=expires_at,
        ),
    )

    assert credential.encrypted_access_token == f"fake-encrypted:{ACCESS_TOKEN[::-1]}"
    assert credential.encrypted_refresh_token == f"fake-encrypted:{REFRESH_TOKEN[::-1]}"
    assert credential.encrypted_access_token != ACCESS_TOKEN
    assert credential.encrypted_refresh_token != REFRESH_TOKEN
    assert not hasattr(credential, "access_token")
    assert not hasattr(credential, "refresh_token")
    assert credential.token_type == "Bearer"
    assert credential.scope == "test.read test.write"
    assert credential.expires_at == expires_at
    assert repository.created == [credential]
    assert repository.saved == []


async def test_read_decrypts_without_mutating_persisted_entity() -> None:
    credential = OAuthCredential(
        publishing_account_id=7,
        encrypted_access_token=f"fake-encrypted:{ACCESS_TOKEN[::-1]}",
        encrypted_refresh_token=f"fake-encrypted:{REFRESH_TOKEN[::-1]}",
        token_type="Bearer",
        scope="test.read",
    )
    original = (credential.encrypted_access_token, credential.encrypted_refresh_token)
    service = make_service(FakeRepository(credential))

    data = await service.get_decrypted_by_publishing_account_id(7)

    assert data is not None
    assert data.access_token == ACCESS_TOKEN
    assert data.refresh_token == REFRESH_TOKEN
    assert data.token_type == "Bearer"
    assert data.scope == "test.read"
    assert (credential.encrypted_access_token, credential.encrypted_refresh_token) == original


async def test_update_without_refresh_token_preserves_existing_ciphertext() -> None:
    credential = OAuthCredential(
        publishing_account_id=7,
        encrypted_access_token="fake-encrypted:old-access",
        encrypted_refresh_token="fake-encrypted:preserved-refresh",
    )
    repository = FakeRepository(credential)

    result = await make_service(repository).store(
        7, OAuthCredentialInput(access_token=ACCESS_TOKEN, refresh_token=None)
    )

    assert result is credential
    assert result.encrypted_access_token == f"fake-encrypted:{ACCESS_TOKEN[::-1]}"
    assert result.encrypted_refresh_token == "fake-encrypted:preserved-refresh"
    assert repository.saved == [credential]


async def test_update_with_refresh_token_rotates_ciphertext() -> None:
    credential = OAuthCredential(
        publishing_account_id=7,
        encrypted_access_token="fake-encrypted:old-access",
        encrypted_refresh_token="fake-encrypted:old-refresh",
    )
    repository = FakeRepository(credential)

    result = await make_service(repository).store(
        7, OAuthCredentialInput(access_token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN)
    )

    assert result.encrypted_refresh_token == f"fake-encrypted:{REFRESH_TOKEN[::-1]}"
    assert repository.saved == [credential]


async def test_encryption_failure_prevents_partial_persistence_and_secret_leakage() -> None:
    repository = FakeRepository()
    service = make_service(repository, FakeEncryptor(fail_on=REFRESH_TOKEN))

    with pytest.raises(CredentialEncryptionError) as error:
        await service.store(
            7, OAuthCredentialInput(access_token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN)
        )

    assert repository.get_calls == 0
    assert repository.created == []
    assert repository.saved == []
    assert ACCESS_TOKEN not in str(error.value)
    assert REFRESH_TOKEN not in repr(error.value)


async def test_decryption_failure_is_controlled_without_entity_mutation() -> None:
    ciphertext = "unreadable-test-ciphertext"
    credential = OAuthCredential(
        publishing_account_id=7,
        encrypted_access_token=ciphertext,
        encrypted_refresh_token=None,
    )
    service = make_service(FakeRepository(credential))

    with pytest.raises(CredentialEncryptionError) as error:
        await service.get_decrypted_by_publishing_account_id(7)

    assert ciphertext not in str(error.value)
    assert credential.encrypted_access_token == ciphertext


async def test_missing_credential_returns_none() -> None:
    assert await make_service(FakeRepository()).get_decrypted_by_publishing_account_id(7) is None
