from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies as dependency_module
from app.api.dependencies import get_broll_retrieval_service
from app.api.v1.endpoints import broll as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    BrollAssetNotFoundError,
    BrollCollectionNotFoundError,
    BrollScriptNotReadyError,
    BrollUnusableScriptError,
    ScriptNotFoundError,
    UnsupportedBrollQueryStrategyError,
)
from app.models.broll import BrollAssetStatus, BrollCollectionStatus


def retrieval_options(**values) -> dict:
    result = {
        "provider": "local",
        "media_type": "video",
        "orientation": "portrait",
        "max_assets_per_section": 3,
        "min_duration_seconds": 0,
        "max_duration_seconds": 60,
        "min_width": 720,
        "min_height": 1280,
        "safe_search": True,
        "download_assets": False,
        "language": "en",
    }
    result.update(values)
    return result


def request_body(**values) -> dict:
    result = {
        "script_id": 4,
        "query_strategy": "section_keywords",
        "options": retrieval_options(),
    }
    result.update(values)
    return result


def collection(state: BrollCollectionStatus, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "script_id": 4,
        "status": state,
        "provider": "local",
        "query_strategy": "section_keywords",
        "retrieval_options": retrieval_options(),
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def asset(state: BrollAssetStatus = BrollAssetStatus.CANDIDATE, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 11,
        "collection_id": 1,
        "script_section_order": 0,
        "provider": "local",
        "external_id": "local-abc",
        "media_type": "video",
        "status": state,
        "query": "city architecture",
        "title": "City architecture",
        "description": "Synthetic candidate",
        "source_url": "https://local.invalid/source/abc",
        "preview_url": "https://local.invalid/preview/abc",
        "download_url": "https://local.invalid/download/abc",
        "storage_key": None,
        "width": 1080,
        "height": 1920,
        "duration_seconds": 5.0,
        "file_size_bytes": None,
        "mime_type": "video/mp4",
        "checksum": None,
        "attribution": "Synthetic metadata",
        "license_name": "Development placeholder",
        "photographer_or_creator": "ClipForge",
        "orientation": "portrait",
        "relevance_score": 1.0,
        "metadata_data": {"synthetic": True},
        "created_at": timestamp,
        "updated_at": timestamp,
        "downloaded_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class FakeService:
    def __init__(
        self,
        current=None,
        *,
        collections=None,
        assets=None,
        should_enqueue=True,
    ) -> None:
        self.current = current
        self.collections = collections or []
        self.assets = assets or []
        self.should_enqueue = should_enqueue
        self.created = None
        self.enqueue_failures = []

    async def request_broll_retrieval(self, script_id, options, *, query_strategy):
        self.created = (script_id, options, query_strategy)
        return self.current

    async def get_collection(self, collection_id):
        return self.current

    async def list_collections_for_script(self, script_id):
        return self.collections

    async def list_assets_for_collection(self, collection_id):
        return [item for item in self.assets if item.collection_id == collection_id]

    async def get_asset(self, asset_id):
        return next(item for item in self.assets if item.id == asset_id)

    async def prepare_collection_retry(self, collection_id):
        if self.should_enqueue:
            self.current.status = BrollCollectionStatus.PENDING
            self.current.completed_at = None
            self.current.error_message = None
        return self.current, self.should_enqueue

    async def mark_broll_enqueue_failed(self, current, error):
        current.status = BrollCollectionStatus.FAILED
        current.completed_at = None
        current.error_message = f"B-roll retrieval task enqueue failed: {error}"
        self.enqueue_failures.append((current, error))

    async def select_asset_and_commit(self, asset_id):
        selected = await self.get_asset(asset_id)
        selected.status = BrollAssetStatus.SELECTED
        return selected

    async def reject_asset_and_commit(self, asset_id):
        rejected = await self.get_asset(asset_id)
        rejected.status = BrollAssetStatus.REJECTED
        return rejected


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_broll_retrieval_service] = lambda: service
    return TestClient(app)


def test_create_queues_pending_collection_and_returns_202(monkeypatch) -> None:
    current = collection(BrollCollectionStatus.PENDING, id=12)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.retrieve_broll, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert service.created[0] == 4
    assert service.created[1].orientation.value == "portrait"
    assert service.created[2] == "section_keywords"
    assert queued == [12]


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ScriptNotFoundError(), 404),
        (BrollScriptNotReadyError(), 409),
        (BrollUnusableScriptError(), 422),
        (UnsupportedBrollQueryStrategyError(), 422),
    ],
)
def test_create_maps_application_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_broll_retrieval(self, script_id, options, *, query_strategy):
            raise error

    with client_for(FailingService()) as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())

    assert response.status_code == expected_status


def test_create_rejects_invalid_request_schema() -> None:
    body = request_body(options=retrieval_options(max_assets_per_section=0))
    with client_for(FakeService()) as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=body)

    assert response.status_code == 422


def test_create_enqueue_failure_returns_503_and_persists_failure(monkeypatch) -> None:
    current = collection(BrollCollectionStatus.PENDING)
    service = FakeService(current)

    def fail(collection_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.retrieve_broll, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())

    assert response.status_code == 503
    assert current.status == BrollCollectionStatus.FAILED
    assert current.completed_at is None
    assert current.error_message == "B-roll retrieval task enqueue failed: broker unavailable"


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (BrollCollectionStatus.PENDING, None),
        (BrollCollectionStatus.SEARCHING, None),
        (BrollCollectionStatus.FAILED, "Search failed"),
    ],
)
def test_get_noncompleted_collection_returns_status(state, message) -> None:
    current = collection(state, error_message=message)
    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/broll-collections/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "script_id": 4,
        "status": state.value,
        "completed_at": None,
        "error_message": message,
    }


def test_get_completed_collection_returns_full_response_with_assets() -> None:
    current = collection(BrollCollectionStatus.COMPLETED, completed_at=datetime.now(UTC))
    with client_for(FakeService(current, assets=[asset()])) as client:
        response = client.get("/api/v1/broll-collections/1")

    assert response.status_code == 200
    assert response.json()["provider"] == "local"
    assert response.json()["assets"][0]["external_id"] == "local-abc"


def test_get_missing_collection_returns_404() -> None:
    class MissingService(FakeService):
        async def get_collection(self, collection_id):
            raise BrollCollectionNotFoundError

    with client_for(MissingService()) as client:
        assert client.get("/api/v1/broll-collections/999").status_code == 404


def test_list_returns_mixed_variants_in_service_order() -> None:
    now = datetime.now(UTC)
    newest = collection(BrollCollectionStatus.PENDING, id=2, created_at=now)
    oldest = collection(
        BrollCollectionStatus.COMPLETED,
        id=1,
        created_at=now - timedelta(minutes=1),
        completed_at=now,
    )
    service = FakeService(collections=[newest, oldest], assets=[asset(collection_id=1)])
    with client_for(service) as client:
        response = client.get("/api/v1/scripts/4/broll-collections")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2, 1]
    assert "assets" not in response.json()[0]
    assert len(response.json()[1]["assets"]) == 1


def test_list_allows_empty_result() -> None:
    with client_for(FakeService(collections=[])) as client:
        response = client.get("/api/v1/scripts/4/broll-collections")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("state", [BrollCollectionStatus.PENDING, BrollCollectionStatus.FAILED])
def test_retry_reenqueues_same_row_and_clears_error(monkeypatch, state) -> None:
    current = collection(state, id=9, error_message="Old error")
    service = FakeService(current, should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.retrieve_broll, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/broll-collections/9/retry")

    assert response.status_code == 202
    assert response.json()["id"] == 9
    assert response.json()["status"] == "pending"
    assert current.error_message is None
    assert queued == [9]


@pytest.mark.parametrize(
    "state", [BrollCollectionStatus.SEARCHING, BrollCollectionStatus.COMPLETED]
)
def test_retry_does_not_enqueue_active_or_completed_collection(monkeypatch, state) -> None:
    current = collection(
        state, completed_at=datetime.now(UTC) if state.value == "completed" else None
    )
    service = FakeService(current, assets=[asset()] if state.value == "completed" else [])
    service.should_enqueue = False
    queued = []
    monkeypatch.setattr(endpoint_module.retrieve_broll, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/broll-collections/1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert queued == []


def test_retry_enqueue_failure_returns_503(monkeypatch) -> None:
    current = collection(BrollCollectionStatus.FAILED, error_message="Old error")
    service = FakeService(current)

    def fail(collection_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.retrieve_broll, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/broll-collections/1/retry")

    assert response.status_code == 503
    assert current.status == BrollCollectionStatus.FAILED
    assert current.error_message == "B-roll retrieval task enqueue failed: broker unavailable"


def test_list_get_select_and_reject_assets_without_sibling_changes() -> None:
    first = asset(id=11, relevance_score=1.0)
    sibling = asset(id=12, relevance_score=0.9)
    service = FakeService(collection(BrollCollectionStatus.COMPLETED), assets=[first, sibling])
    with client_for(service) as client:
        listed = client.get("/api/v1/broll-collections/1/assets")
        fetched = client.get("/api/v1/broll-assets/11")
        selected = client.post("/api/v1/broll-assets/11/select")
        rejected = client.post("/api/v1/broll-assets/11/reject")

    assert [item["id"] for item in listed.json()] == [11, 12]
    assert fetched.json()["id"] == 11
    assert selected.json()["status"] == "selected"
    assert rejected.json()["status"] == "rejected"
    assert sibling.status == BrollAssetStatus.CANDIDATE
    assert first.storage_key is None


def test_missing_asset_returns_404() -> None:
    class MissingService(FakeService):
        async def get_asset(self, asset_id):
            raise BrollAssetNotFoundError

    with client_for(MissingService()) as client:
        assert client.get("/api/v1/broll-assets/999").status_code == 404


def test_list_assets_missing_collection_returns_404() -> None:
    class MissingService(FakeService):
        async def list_assets_for_collection(self, collection_id):
            raise BrollCollectionNotFoundError

    with client_for(MissingService()) as client:
        assert client.get("/api/v1/broll-collections/999/assets").status_code == 404


def test_dependency_factory_composes_repositories_and_local_provider(monkeypatch) -> None:
    session = object()
    script_repository = object()
    collection_repository = object()
    asset_repository = object()
    provider = object()

    monkeypatch.setattr(dependency_module, "ScriptRepository", lambda received: script_repository)
    monkeypatch.setattr(
        dependency_module,
        "BrollCollectionRepository",
        lambda received: collection_repository,
    )
    monkeypatch.setattr(
        dependency_module, "BrollAssetRepository", lambda received: asset_repository
    )
    monkeypatch.setattr(dependency_module, "LocalMediaProvider", lambda: provider)

    service = dependency_module.get_broll_retrieval_service(session)

    assert service.script_repository is script_repository
    assert service.collection_repository is collection_repository
    assert service.asset_repository is asset_repository
    assert service.media_provider is provider
