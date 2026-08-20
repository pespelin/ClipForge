from typing import Protocol


class PublishingAccessCredential(Protocol):
    """Minimum usable credential surface exposed to a publishing adapter."""

    access_token: str
    token_type: str | None


class PublishingCredentialResolver(Protocol):
    """Resolve a provider-facing credential from an opaque account reference."""

    async def resolve(self, account_reference: str) -> PublishingAccessCredential: ...


class PublishingArtifactReader(Protocol):
    """Read a persisted publishing artifact without exposing storage details."""

    async def read(self, storage_key: str) -> bytes: ...
