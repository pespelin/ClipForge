from urllib.parse import parse_qs

import httpx
import pytest

from app.providers.oauth import (
    GoogleOAuthTokenExchangeProvider,
    OAuthTokenExchangeAuthenticationError,
    OAuthTokenExchangeError,
    OAuthTokenExchangeProvider,
    OAuthTokenExchangeRateLimitError,
    OAuthTokenExchangeTransientError,
)

CODE = "clipforge-test-authorization-code-never-real"
SECRET = "clipforge-test-client-secret-never-real"
VERIFIER = "clipforge-test-pkce-verifier-never-real"
ACCESS_TOKEN = "clipforge-test-access-token-never-real"
REFRESH_TOKEN = "clipforge-test-refresh-token-never-real"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def make_provider(handler) -> tuple[OAuthTokenExchangeProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0)
    provider = GoogleOAuthTokenExchangeProvider(
        client,
        client_id="clipforge-test-client-id",
        client_secret=SECRET,
        redirect_uri="https://clipforge.test/oauth/callback",
    )
    return provider, client


async def test_google_token_exchange_posts_form_and_normalizes_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == TOKEN_URL
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert parse_qs(request.content.decode()) == {
            "client_id": ["clipforge-test-client-id"],
            "client_secret": [SECRET],
            "code": [CODE],
            "code_verifier": [VERIFIER],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["https://clipforge.test/oauth/callback"],
        }
        assert CODE not in str(request.url)
        assert SECRET not in str(request.url)
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/youtube.upload",
                "expires_in": 3600,
            },
        )

    provider, client = make_provider(handler)
    async with client:
        result = await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)

    assert result.access_token == ACCESS_TOKEN
    assert result.refresh_token == REFRESH_TOKEN
    assert result.token_type == "Bearer"
    assert result.scope == "https://www.googleapis.com/auth/youtube.upload"
    assert result.expires_in == 3600
    assert ACCESS_TOKEN not in repr(result)
    assert REFRESH_TOKEN not in repr(result)


async def test_google_token_exchange_accepts_missing_refresh_token() -> None:
    provider, client = make_provider(
        lambda _: httpx.Response(200, json={"access_token": ACCESS_TOKEN})
    )
    async with client:
        result = await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)

    assert result.refresh_token is None
    assert result.expires_in is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": ""},
        {"access_token": 123},
        {"access_token": ACCESS_TOKEN, "expires_in": "3600"},
        {"access_token": ACCESS_TOKEN, "expires_in": 0},
        {"access_token": ACCESS_TOKEN, "refresh_token": 123},
        {"error": "invalid_grant", "error_description": CODE},
    ],
)
async def test_google_token_exchange_rejects_invalid_or_oauth_error_payload(payload) -> None:
    provider, client = make_provider(lambda _: httpx.Response(200, json=payload))
    async with client:
        with pytest.raises(OAuthTokenExchangeError) as error:
            await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)

    assert str(error.value) == "OAuth token exchange failed"
    for secret in (CODE, SECRET, VERIFIER, ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in repr(error.value)


async def test_google_token_exchange_rejects_non_success_and_malformed_json() -> None:
    for response in (
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, content=b"{"),
    ):
        provider, client = make_provider(lambda _, response=response: response)
        async with client:
            with pytest.raises(OAuthTokenExchangeError):
                await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)


@pytest.mark.parametrize("transport_error", ["timeout", "connection"])
async def test_google_token_exchange_translates_transport_errors(transport_error: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if transport_error == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        raise httpx.ConnectError("test connection failure", request=request)

    provider, client = make_provider(handler)
    async with client:
        with pytest.raises(OAuthTokenExchangeError) as error:
            await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)

    assert "test" not in str(error.value).lower()
    assert CODE not in repr(error.value)


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (
            httpx.Response(400, json={"error": "invalid_grant"}),
            OAuthTokenExchangeAuthenticationError,
        ),
        (httpx.Response(429, headers={"Retry-After": "30"}), OAuthTokenExchangeRateLimitError),
        (httpx.Response(503), OAuthTokenExchangeTransientError),
    ],
)
async def test_google_token_exchange_classifies_safe_failure_categories(
    response: httpx.Response, expected_error
) -> None:
    provider, client = make_provider(lambda _: response)
    async with client:
        with pytest.raises(expected_error) as error:
            await provider.exchange_code(authorization_code=CODE, code_verifier=VERIFIER)

    assert CODE not in repr(error.value)
    assert SECRET not in repr(error.value)
