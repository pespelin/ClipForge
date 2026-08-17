import inspect

import pytest

from app.security import CredentialEncryptionError, CredentialEncryptor


class FakeCredentialEncryptor:
    """Deterministic contract double; this is not production encryption."""

    _PREFIX = "fake-encrypted-v1:"

    def encrypt(self, plaintext: str) -> str:
        return f"{self._PREFIX}{plaintext[::-1]}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(self._PREFIX):
            raise CredentialEncryptionError
        return ciphertext.removeprefix(self._PREFIX)[::-1]


def test_encryptor_contract_is_small_and_synchronous() -> None:
    encryptor: CredentialEncryptor = FakeCredentialEncryptor()

    assert callable(encryptor.encrypt)
    assert callable(encryptor.decrypt)
    assert not inspect.iscoroutinefunction(encryptor.encrypt)
    assert not inspect.iscoroutinefunction(encryptor.decrypt)


def test_fake_encryptor_exercises_round_trip_without_plaintext_output() -> None:
    plaintext = "clipforge-test-secret-never-real"
    encryptor: CredentialEncryptor = FakeCredentialEncryptor()

    ciphertext = encryptor.encrypt(plaintext)

    assert ciphertext != plaintext
    assert plaintext not in ciphertext
    assert encryptor.decrypt(ciphertext) == plaintext


def test_encryption_error_is_controlled_and_does_not_leak_secret() -> None:
    plaintext = "clipforge-test-secret-never-real"
    encryptor: CredentialEncryptor = FakeCredentialEncryptor()

    with pytest.raises(CredentialEncryptionError) as error:
        encryptor.decrypt("malformed-test-payload")

    assert plaintext not in str(error.value)
    assert plaintext not in repr(error.value)
    assert str(error.value) == "Credential encryption operation failed"
