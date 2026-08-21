from urllib.parse import parse_qs

import httpx
import pytest

from app.providers.oauth import (
    GoogleOAuthTokenRefreshProvider,
    OAuthTokenRefreshAuthenticationError,
    OAuthTokenRefreshError,
    OAuthTokenRefreshProvider,
    OAuthTokenRefreshRateLimitError,
    OAuthTokenRefreshTransientError,
)

SECRET = "clipforge-test-client-secret-never-real"
REFRESH_TOKEN = "clipforge-test-refresh-token-never-real"
ACCESS_TOKEN = "clipforge-test-access-token-never-real"
ROTATED_REFRESH_TOKEN = "clipforge-test-rotated-refresh-token-never-real"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def make_provider(handler) -> tuple[OAuthTokenRefreshProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0)
    provider = GoogleOAuthTokenRefreshProvider(
        client,
        client_id="clipforge-test-client-id",
        client_secret=SECRET,
    )
    return provider, client


async def test_google_refresh_posts_only_refresh_grant_fields_and_normalizes_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == TOKEN_URL
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        form = parse_qs(request.content.decode())
        assert form == {
            "client_id": ["clipforge-test-client-id"],
            "client_secret": [SECRET],
            "refresh_token": [REFRESH_TOKEN],
            "grant_type": ["refresh_token"],
        }
        for forbidden in ("code", "code_verifier", "redirect_uri"):
            assert forbidden not in form
        assert SECRET not in str(request.url)
        assert REFRESH_TOKEN not in str(request.url)
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": ROTATED_REFRESH_TOKEN,
                "token_type": "Bearer",
                "scope": "youtube.upload",
                "expires_in": 3600,
            },
        )

    provider, client = make_provider(handler)
    async with client:
        result = await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert result.access_token == ACCESS_TOKEN
    assert result.refresh_token == ROTATED_REFRESH_TOKEN
    assert result.expires_in == 3600
    assert ACCESS_TOKEN not in repr(result)
    assert ROTATED_REFRESH_TOKEN not in repr(result)


async def test_google_refresh_accepts_missing_rotated_refresh_token() -> None:
    provider, client = make_provider(
        lambda _: httpx.Response(200, json={"access_token": ACCESS_TOKEN})
    )
    async with client:
        result = await provider.refresh_token(refresh_token=REFRESH_TOKEN)

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
        {"error": "invalid_grant", "error_description": REFRESH_TOKEN},
    ],
)
async def test_google_refresh_rejects_invalid_or_oauth_error_payload(payload) -> None:
    provider, client = make_provider(lambda _: httpx.Response(200, json=payload))
    async with client:
        with pytest.raises(OAuthTokenRefreshError) as error:
            await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert str(error.value) == "OAuth token refresh failed"
    for secret in (SECRET, REFRESH_TOKEN, ACCESS_TOKEN, ROTATED_REFRESH_TOKEN):
        assert secret not in repr(error.value)


async def test_google_refresh_rejects_non_success_and_malformed_json() -> None:
    responses = (
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, content=b"{"),
    )
    for response in responses:
        provider, client = make_provider(lambda _, response=response: response)
        async with client:
            with pytest.raises(OAuthTokenRefreshError):
                await provider.refresh_token(refresh_token=REFRESH_TOKEN)


@pytest.mark.parametrize("transport_error", ["timeout", "connection"])
async def test_google_refresh_translates_transport_errors(transport_error: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if transport_error == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        raise httpx.ConnectError("test connection failure", request=request)

    provider, client = make_provider(handler)
    async with client:
        with pytest.raises(OAuthTokenRefreshError) as error:
            await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert "test" not in str(error.value).lower()
    assert REFRESH_TOKEN not in repr(error.value)


async def test_google_refresh_invalid_grant_requires_reconnect_without_leakage() -> None:
    provider, client = make_provider(
        lambda _: httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": REFRESH_TOKEN},
        )
    )
    async with client:
        with pytest.raises(OAuthTokenRefreshAuthenticationError) as error:
            await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert REFRESH_TOKEN not in repr(error.value)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_google_refresh_server_failures_are_transient(status: int) -> None:
    provider, client = make_provider(lambda _: httpx.Response(status, text=REFRESH_TOKEN))
    async with client:
        with pytest.raises(OAuthTokenRefreshTransientError) as error:
            await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert REFRESH_TOKEN not in repr(error.value)


async def test_google_refresh_rate_limit_exposes_only_safe_retry_after() -> None:
    provider, client = make_provider(
        lambda _: httpx.Response(
            429,
            headers={"Retry-After": "45"},
            text=REFRESH_TOKEN,
        )
    )
    async with client:
        with pytest.raises(OAuthTokenRefreshRateLimitError) as error:
            await provider.refresh_token(refresh_token=REFRESH_TOKEN)

    assert error.value.retry_after_seconds == 45
    assert REFRESH_TOKEN not in repr(error.value)
