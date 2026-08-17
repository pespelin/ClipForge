import inspect

import pytest

from app.security import (
    CredentialEncryptionError,
    CredentialEncryptor,
    FernetCredentialEncryptor,
)

TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OTHER_TEST_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="


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


def test_fernet_encryptor_round_trip_protects_plaintext() -> None:
    plaintext = "clipforge-test-refresh-token-never-real"
    encryptor: CredentialEncryptor = FernetCredentialEncryptor(TEST_KEY)

    first = encryptor.encrypt(plaintext)
    second = encryptor.encrypt(plaintext)

    assert first != plaintext
    assert plaintext not in first
    assert first != second
    assert encryptor.decrypt(first) == plaintext
    assert encryptor.decrypt(second) == plaintext


def test_fernet_encryptor_supports_empty_plaintext() -> None:
    encryptor = FernetCredentialEncryptor(TEST_KEY)

    assert encryptor.decrypt(encryptor.encrypt("")) == ""


@pytest.mark.parametrize("ciphertext", ["malformed-test-payload", "", "şifreli-değil"])
def test_fernet_encryptor_translates_malformed_ciphertext(ciphertext: str) -> None:
    with pytest.raises(CredentialEncryptionError):
        FernetCredentialEncryptor(TEST_KEY).decrypt(ciphertext)


def test_fernet_encryptor_detects_tampering_without_secret_leakage() -> None:
    plaintext = "clipforge-test-refresh-token-never-real"
    ciphertext = FernetCredentialEncryptor(TEST_KEY).encrypt(plaintext)
    replacement = "B" if ciphertext[20] == "A" else "A"
    tampered = f"{ciphertext[:20]}{replacement}{ciphertext[21:]}"

    with pytest.raises(CredentialEncryptionError) as error:
        FernetCredentialEncryptor(TEST_KEY).decrypt(tampered)

    assert plaintext not in str(error.value)
    assert plaintext not in repr(error.value)
    assert ciphertext not in str(error.value)


def test_fernet_encryptor_translates_wrong_key_without_secret_leakage() -> None:
    plaintext = "clipforge-test-refresh-token-never-real"
    ciphertext = FernetCredentialEncryptor(TEST_KEY).encrypt(plaintext)

    with pytest.raises(CredentialEncryptionError) as error:
        FernetCredentialEncryptor(OTHER_TEST_KEY).decrypt(ciphertext)

    assert plaintext not in str(error.value)
    assert TEST_KEY not in str(error.value)
    assert OTHER_TEST_KEY not in repr(error.value)


@pytest.mark.parametrize(
    "invalid_key", ["invalid-test-key", "", "not-a-fernet-key", "şifre-test-key"]
)
def test_fernet_encryptor_translates_invalid_constructor_key(invalid_key: str) -> None:
    with pytest.raises(CredentialEncryptionError) as error:
        FernetCredentialEncryptor(invalid_key)

    assert str(error.value) == "Credential encryption operation failed"
    if invalid_key:
        assert invalid_key not in repr(error.value)
