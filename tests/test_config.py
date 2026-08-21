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
    assert settings.publishing_execution_lease_seconds == 900
    assert settings.youtube_oauth_client_id is None
    assert settings.youtube_oauth_client_secret is None
    assert settings.youtube_oauth_redirect_uri is None
    assert settings.oauth_state_ttl_seconds == 600
    assert settings.credential_encryption_key is None


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


def test_publishing_execution_lease_is_parsed_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLISHING_EXECUTION_LEASE_SECONDS", "1200")

    assert get_settings().publishing_execution_lease_seconds == 1200


@pytest.mark.parametrize("invalid_seconds", ["0", "-1"])
def test_publishing_execution_lease_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, invalid_seconds: str
) -> None:
    monkeypatch.setenv("PUBLISHING_EXECUTION_LEASE_SECONDS", invalid_seconds)

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


def test_credential_encryption_key_is_parsed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", test_key)

    settings = get_settings()

    assert isinstance(settings.credential_encryption_key, SecretStr)
    assert settings.credential_encryption_key.get_secret_value() == test_key
    assert test_key not in repr(settings)
    assert test_key not in repr(settings.credential_encryption_key)


def test_oauth_state_ttl_is_parsed_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_STATE_TTL_SECONDS", "900")

    assert get_settings().oauth_state_ttl_seconds == 900


@pytest.mark.parametrize("invalid_ttl", ["0", "-1"])
def test_oauth_state_ttl_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, invalid_ttl: str
) -> None:
    monkeypatch.setenv("OAUTH_STATE_TTL_SECONDS", invalid_ttl)

    with pytest.raises(ValidationError):
        get_settings()
