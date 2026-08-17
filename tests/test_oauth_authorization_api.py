import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies as dependency_module
from app.api.dependencies import get_oauth_authorization_service
from app.api.v1.endpoints import oauth_authorization as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    OAuthAuthorizationConfigurationError,
    OAuthAuthorizationUnavailableError,
    PublishingAccountInactiveError,
    PublishingAccountNotFoundError,
    UnsupportedOAuthPublishingPlatformError,
)
from app.services.oauth_authorization_service import OAuthAuthorizationResult


class FakeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.account_ids: list[int] = []

    async def authorize(self, publishing_account_id: int) -> OAuthAuthorizationResult:
        self.account_ids.append(publishing_account_id)
        if self.error is not None:
            raise self.error
        return OAuthAuthorizationResult(
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth?state=test-state"
        )


def client_for(service: FakeService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_oauth_authorization_service] = lambda: service
    return TestClient(app)


def test_authorization_endpoint_returns_only_authorization_url() -> None:
    service = FakeService()

    with client_for(service) as client:
        response = client.post("/api/v1/publishing-accounts/7/oauth/authorize")

    assert response.status_code == 200
    assert response.json() == {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?state=test-state"
    }
    assert service.account_ids == [7]
    for forbidden in ("code_verifier", "client_secret", "access_token", "refresh_token"):
        assert forbidden not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (PublishingAccountNotFoundError(), 404, "Publishing account not found"),
        (PublishingAccountInactiveError(), 409, "Publishing account is inactive"),
        (
            UnsupportedOAuthPublishingPlatformError(),
            409,
            "Publishing account platform does not support OAuth authorization",
        ),
        (
            OAuthAuthorizationConfigurationError(),
            503,
            "OAuth authorization is not configured",
        ),
        (
            OAuthAuthorizationUnavailableError(),
            503,
            "OAuth authorization is temporarily unavailable",
        ),
    ],
)
def test_authorization_endpoint_maps_service_errors(
    error: Exception, expected_status: int, expected_detail: str
) -> None:
    with client_for(FakeService(error)) as client:
        response = client.post("/api/v1/publishing-accounts/7/oauth/authorize")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "code_verifier" not in response.text


def test_authorization_endpoint_has_no_session_or_transaction_dependency() -> None:
    service = AsyncMock()
    service.authorize.return_value = OAuthAuthorizationResult(
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth"
    )

    with client_for(service) as client:  # type: ignore[arg-type]
        response = client.post("/api/v1/publishing-accounts/7/oauth/authorize")

    assert response.status_code == 200
    service.authorize.assert_awaited_once_with(7)


def test_dependency_composes_existing_session_redis_client_and_provider(monkeypatch) -> None:
    session = object()
    redis_client = object()
    repository = object()
    state_store = object()
    provider = object()
    service = object()
    monkeypatch.setattr(
        dependency_module,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_oauth_client_id="test-client-id",
            youtube_oauth_redirect_uri="https://clipforge.test/oauth/callback",
            oauth_state_ttl_seconds=600,
        ),
    )
    repository_factory = Mock(return_value=repository)
    store_factory = Mock(return_value=state_store)
    provider_factory = Mock(return_value=provider)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(dependency_module, "PublishingAccountRepository", repository_factory)
    monkeypatch.setattr(dependency_module, "RedisOAuthAuthorizationStateStore", store_factory)
    monkeypatch.setattr(dependency_module, "GoogleOAuthAuthorizationProvider", provider_factory)
    monkeypatch.setattr(dependency_module, "OAuthAuthorizationService", service_factory)

    result = dependency_module.get_oauth_authorization_service(session, redis_client)

    assert result is service
    repository_factory.assert_called_once_with(session)
    store_factory.assert_called_once_with(redis_client)
    provider_factory.assert_called_once_with()
    service_factory.assert_called_once_with(
        account_repository=repository,
        state_store=state_store,
        authorization_provider=provider,
        client_id="test-client-id",
        redirect_uri="https://clipforge.test/oauth/callback",
        state_ttl_seconds=600,
    )


def test_authorization_route_contains_only_service_and_http_mapping() -> None:
    source = inspect.getsource(endpoint_module)

    assert "Repository(" not in source
    assert "Redis" not in source
    assert "GoogleOAuth" not in source
    assert "commit(" not in source
    assert "code_verifier" not in source
