from urllib.parse import parse_qs, urlparse

from app.providers.oauth import GoogleOAuthAuthorizationProvider, OAuthAuthorizationProvider


def test_google_provider_builds_minimum_offline_pkce_authorization_url() -> None:
    provider: OAuthAuthorizationProvider = GoogleOAuthAuthorizationProvider()

    authorization_url = provider.build_authorization_url(
        client_id="clipforge-test-client-id",
        redirect_uri="https://clipforge.test/oauth/callback",
        state="test-state",
        code_challenge="test-code-challenge",
        scopes=("https://www.googleapis.com/auth/youtube.upload",),
    )
    parsed = urlparse(authorization_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"
    assert query == {
        "access_type": ["offline"],
        "client_id": ["clipforge-test-client-id"],
        "code_challenge": ["test-code-challenge"],
        "code_challenge_method": ["S256"],
        "redirect_uri": ["https://clipforge.test/oauth/callback"],
        "response_type": ["code"],
        "scope": ["https://www.googleapis.com/auth/youtube.upload"],
        "state": ["test-state"],
    }
    for forbidden in (
        "client_secret",
        "code_verifier",
        "credential_encryption_key",
        "refresh_token",
        "access_token",
    ):
        assert forbidden not in query
        assert forbidden not in authorization_url
