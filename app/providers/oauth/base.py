from dataclasses import dataclass, field
from typing import Protocol


class OAuthTokenExchangeError(Exception):
    """Raised when an authorization code cannot be exchanged safely."""

    def __init__(self) -> None:
        super().__init__("OAuth token exchange failed")


class OAuthTokenExchangeTransientError(OAuthTokenExchangeError):
    """Raised when code exchange may succeed on a later attempt."""


class OAuthTokenExchangeRateLimitError(OAuthTokenExchangeTransientError):
    """Raised when the token endpoint rate limits code exchange."""

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class OAuthTokenExchangeAuthenticationError(OAuthTokenExchangeError):
    """Raised when the supplied authorization grant is invalid."""


class OAuthTokenRefreshError(Exception):
    """Raised when an OAuth access token cannot be refreshed safely."""

    def __init__(self) -> None:
        super().__init__("OAuth token refresh failed")


class OAuthTokenRefreshTransientError(OAuthTokenRefreshError):
    """Raised when refreshing may succeed on a later attempt."""


class OAuthTokenRefreshRateLimitError(OAuthTokenRefreshTransientError):
    """Raised when the token endpoint rate limits refresh attempts."""

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class OAuthTokenRefreshAuthenticationError(OAuthTokenRefreshError):
    """Raised when the stored refresh grant requires account reconnection."""


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


class OAuthTokenRefreshProvider(Protocol):
    """Provider boundary for refreshing an OAuth access token."""

    async def refresh_token(self, *, refresh_token: str) -> OAuthTokenResult:
        """Refresh an access token without exposing transport details."""
        ...
