from typing import Protocol


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
