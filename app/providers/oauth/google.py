from urllib.parse import urlencode

import httpx

from app.providers.oauth.base import OAuthTokenExchangeError, OAuthTokenResult


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


class GoogleOAuthTokenExchangeProvider:
    """Async Google authorization-code token exchange adapter."""

    _TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    async def exchange_code(
        self, *, authorization_code: str, code_verifier: str
    ) -> OAuthTokenResult:
        try:
            response = await self._client.post(
                self._TOKEN_ENDPOINT,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": authorization_code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                },
            )
        except (httpx.TimeoutException, httpx.RequestError):
            raise OAuthTokenExchangeError from None

        if not response.is_success:
            raise OAuthTokenExchangeError
        try:
            payload = response.json()
            return self._parse_token_result(payload)
        except (TypeError, ValueError):
            raise OAuthTokenExchangeError from None

    @staticmethod
    def _parse_token_result(payload: object) -> OAuthTokenResult:
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise ValueError

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        token_type = payload.get("token_type")
        scope = payload.get("scope")
        expires_in = payload.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError
        for optional_text in (refresh_token, token_type, scope):
            if optional_text is not None and (
                not isinstance(optional_text, str) or not optional_text
            ):
                raise ValueError
        if expires_in is not None and (
            not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0
        ):
            raise ValueError
        return OAuthTokenResult(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            scope=scope,
            expires_in=expires_in,
        )
