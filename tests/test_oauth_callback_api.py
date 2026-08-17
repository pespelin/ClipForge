import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import dependencies as dependency_module
from app.api.dependencies import get_oauth_callback_service
from app.api.v1.endpoints import oauth_callback as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    OAuthCallbackConfigurationError,
    OAuthConsentDeniedError,
    OAuthCredentialPersistenceError,
    OAuthInvalidStateError,
    OAuthTokenExchangeFailedError,
)
from app.db.session import get_db_session
from app.services.oauth_callback_service import OAuthCallbackResult


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    async def complete(self, **parameters):
        self.calls.append(parameters)
        if self.error is not None:
            raise self.error
        return OAuthCallbackResult(publishing_account_id=7)


def client_for(service: FakeService, session: FakeSession) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_oauth_callback_service] = lambda: service
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


def test_callback_success_commits_and_returns_only_safe_result() -> None:
    service = FakeService()
    session = FakeSession()

    with client_for(service, session) as client:
        response = client.get(
            "/api/v1/oauth/youtube/callback",
            params={"state": "test-state", "code": "test-code"},
        )

    assert response.status_code == 200
    assert response.json() == {"publishing_account_id": 7, "connected": True}
    assert service.calls == [
        {
            "state": "test-state",
            "authorization_code": "test-code",
            "provider_error": None,
        }
    ]
    assert session.commits == 1
    assert session.rollbacks == 0
    for forbidden in (
        "test-code",
        "state",
        "access_token",
        "refresh_token",
        "code_verifier",
        "client_secret",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (OAuthInvalidStateError(), 400),
        (OAuthConsentDeniedError(), 400),
        (OAuthTokenExchangeFailedError(), 502),
        (OAuthCallbackConfigurationError(), 503),
        (OAuthCredentialPersistenceError(), 500),
    ],
)
def test_callback_failures_rollback_and_return_safe_errors(
    error: Exception, status_code: int
) -> None:
    session = FakeSession()

    with client_for(FakeService(error), session) as client:
        response = client.get(
            "/api/v1/oauth/youtube/callback",
            params={"state": "test-state", "code": "test-code"},
        )

    assert response.status_code == status_code
    assert session.commits == 0
    assert session.rollbacks == 1
    assert "test-code" not in response.text
    assert "test-state" not in response.text


def test_callback_commit_failure_attempts_rollback() -> None:
    session = FakeSession(commit_error=RuntimeError("test commit failure"))

    with client_for(FakeService(), session) as client:
        response = client.get(
            "/api/v1/oauth/youtube/callback",
            params={"state": "test-state", "code": "test-code"},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert session.commits == 1
    assert session.rollbacks == 1


def test_dependency_composes_same_session_and_existing_redis_http_clients(monkeypatch) -> None:
    session = object()
    redis_client = object()
    http_client = object()
    objects = {
        "account_repository": object(),
        "state_store": object(),
        "token_provider": object(),
        "credential_repository": object(),
        "encryptor": object(),
        "credential_service": object(),
        "callback_service": object(),
    }
    monkeypatch.setattr(
        dependency_module,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_oauth_client_id="test-client-id",
            youtube_oauth_client_secret=SecretStr("test-client-secret"),
            youtube_oauth_redirect_uri="https://clipforge.test/oauth/callback",
            credential_encryption_key=SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
        ),
    )
    factories = {
        "PublishingAccountRepository": Mock(return_value=objects["account_repository"]),
        "RedisOAuthAuthorizationStateStore": Mock(return_value=objects["state_store"]),
        "GoogleOAuthTokenExchangeProvider": Mock(return_value=objects["token_provider"]),
        "OAuthCredentialRepository": Mock(return_value=objects["credential_repository"]),
        "FernetCredentialEncryptor": Mock(return_value=objects["encryptor"]),
        "OAuthCredentialService": Mock(return_value=objects["credential_service"]),
        "OAuthCallbackService": Mock(return_value=objects["callback_service"]),
    }
    for name, factory in factories.items():
        monkeypatch.setattr(dependency_module, name, factory)

    result = dependency_module.get_oauth_callback_service(session, redis_client, http_client)

    assert result is objects["callback_service"]
    factories["PublishingAccountRepository"].assert_called_once_with(session)
    factories["OAuthCredentialRepository"].assert_called_once_with(session)
    factories["RedisOAuthAuthorizationStateStore"].assert_called_once_with(redis_client)
    factories["GoogleOAuthTokenExchangeProvider"].assert_called_once_with(
        http_client,
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="https://clipforge.test/oauth/callback",
    )
    factories["OAuthCallbackService"].assert_called_once_with(
        account_repository=objects["account_repository"],
        state_store=objects["state_store"],
        token_exchange_provider=objects["token_provider"],
        credential_service=objects["credential_service"],
    )


async def test_oauth_http_client_has_finite_timeout_and_closes() -> None:
    dependency = dependency_module.get_oauth_http_client()

    client = await anext(dependency)

    assert client.timeout.connect == 10.0
    assert client.timeout.read == 10.0
    await dependency.aclose()
    assert client.is_closed


@pytest.mark.parametrize(
    "missing_field",
    [
        "youtube_oauth_client_id",
        "youtube_oauth_client_secret",
        "youtube_oauth_redirect_uri",
        "credential_encryption_key",
    ],
)
def test_dependency_rejects_missing_callback_configuration(monkeypatch, missing_field: str) -> None:
    settings = {
        "youtube_oauth_client_id": "test-client-id",
        "youtube_oauth_client_secret": SecretStr("test-client-secret"),
        "youtube_oauth_redirect_uri": "https://clipforge.test/oauth/callback",
        "credential_encryption_key": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    }
    settings[missing_field] = None
    monkeypatch.setattr(dependency_module, "get_settings", lambda: SimpleNamespace(**settings))

    with pytest.raises(OAuthCallbackConfigurationError):
        dependency_module.get_oauth_callback_service(object(), object(), object())


def test_callback_endpoint_contains_only_service_transaction_and_http_mapping() -> None:
    source = inspect.getsource(endpoint_module)

    assert "Repository(" not in source
    assert "Redis" not in source
    assert "GoogleOAuth" not in source
    assert "Fernet" not in source
    assert "session.commit()" in source
    assert "session.rollback()" in source
    assert "access_token" not in source
    assert "refresh_token" not in source
