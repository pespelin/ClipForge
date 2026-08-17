from typing import Protocol


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
