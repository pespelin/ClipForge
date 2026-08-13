import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_youtube_settings_default_to_local_and_none() -> None:
    settings = get_settings()

    assert settings.publishing_provider == "local"
    assert settings.youtube_oauth_client_id is None
    assert settings.youtube_oauth_client_secret is None
    assert settings.youtube_oauth_redirect_uri is None


@pytest.mark.parametrize("provider", ["local", "youtube"])
def test_supported_publishing_providers_are_accepted(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setenv("PUBLISHING_PROVIDER", provider)

    assert get_settings().publishing_provider == provider


def test_unsupported_publishing_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLISHING_PROVIDER", "unsupported")

    with pytest.raises(ValidationError):
        get_settings()


def test_youtube_oauth_fields_are_parsed_and_secret_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_secret = "clipforge-test-secret-do-not-use"
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "clipforge-test-client-id")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", test_secret)
    monkeypatch.setenv(
        "YOUTUBE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v1/oauth/youtube/callback"
    )

    settings = get_settings()

    assert settings.youtube_oauth_client_id == "clipforge-test-client-id"
    assert isinstance(settings.youtube_oauth_client_secret, SecretStr)
    assert settings.youtube_oauth_client_secret.get_secret_value() == test_secret
    assert (
        settings.youtube_oauth_redirect_uri == "http://localhost:8000/api/v1/oauth/youtube/callback"
    )
    assert test_secret not in repr(settings)
    assert test_secret not in repr(settings.youtube_oauth_client_secret)
