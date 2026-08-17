from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


class CredentialEncryptionError(Exception):
    """Raised when a credential encryption operation cannot complete safely."""

    def __init__(self) -> None:
        super().__init__("Credential encryption operation failed")


class CredentialEncryptor(Protocol):
    """Vendor-neutral boundary for protecting credential values at rest."""

    def encrypt(self, plaintext: str) -> str:
        """Return an opaque, persistence-safe encrypted payload."""
        ...

    def decrypt(self, ciphertext: str) -> str:
        """Return plaintext or raise CredentialEncryptionError safely."""
        ...


class FernetCredentialEncryptor:
    """Production credential encryption adapter backed by authenticated Fernet tokens."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError, UnicodeError):
            raise CredentialEncryptionError from None

    def encrypt(self, plaintext: str) -> str:
        try:
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        except (TypeError, ValueError, UnicodeError):
            raise CredentialEncryptionError from None

    def decrypt(self, ciphertext: str) -> str:
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("ascii"))
            return plaintext.decode("utf-8")
        except (InvalidToken, TypeError, ValueError, UnicodeError):
            raise CredentialEncryptionError from None
