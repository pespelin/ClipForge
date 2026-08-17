import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Protocol


class OAuthStateStoreError(Exception):
    """Raised when authorization state cannot be stored or consumed safely."""

    def __init__(self) -> None:
        super().__init__("OAuth authorization state store operation failed")


@dataclass(frozen=True, slots=True)
class OAuthAuthorizationState:
    """Short-lived correlation data for an OAuth authorization request."""

    state: str
    code_verifier: str
    publishing_account_id: int


class OAuthAuthorizationStateStore(Protocol):
    """Provider-neutral boundary for single-use OAuth authorization state."""

    async def save(self, record: OAuthAuthorizationState, ttl_seconds: int) -> None:
        """Store a record for the supplied lifetime."""
        ...

    async def consume(self, state: str) -> OAuthAuthorizationState | None:
        """Atomically remove and return a record, or return None when absent."""
        ...


def generate_oauth_state() -> str:
    """Return a URL-safe OAuth correlation token with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def generate_pkce_code_verifier() -> str:
    """Return an RFC 7636-compatible URL-safe PKCE verifier."""
    return secrets.token_urlsafe(64)


def create_pkce_code_challenge(code_verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for a PKCE verifier."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
