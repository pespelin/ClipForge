from urllib.parse import urlencode


class GoogleOAuthAuthorizationProvider:
    """Offline Google OAuth authorization URL adapter."""

    _AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"

    def build_authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        query = urlencode(
            {
                "access_type": "offline",
                "client_id": client_id,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
            }
        )
        return f"{self._AUTHORIZATION_ENDPOINT}?{query}"
