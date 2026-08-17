from dataclasses import dataclass, field
from typing import Protocol


class OAuthTokenExchangeError(Exception):
    """Raised when an authorization code cannot be exchanged safely."""

    def __init__(self) -> None:
        super().__init__("OAuth token exchange failed")


@dataclass(frozen=True, slots=True)
class OAuthTokenResult:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str | None = None
    scope: str | None = None
    expires_in: int | None = None


class OAuthAuthorizationProvider(Protocol):
    """Provider boundary for creating OAuth authorization URLs without network I/O."""

    def build_authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        """Build an authorization URL for the supplied request parameters."""
        ...


class OAuthTokenExchangeProvider(Protocol):
    """Provider boundary for exchanging an OAuth authorization code."""

    async def exchange_code(
        self, *, authorization_code: str, code_verifier: str
    ) -> OAuthTokenResult:
        """Exchange a code without exposing provider or transport details."""
        ...
