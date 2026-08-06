import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_broll_retrieval_service
from app.api.v1.endpoints import broll as broll_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import BrollRetrievalError
from app.models.broll import (
    BrollAsset,
    BrollAssetStatus,
    BrollCollection,
    BrollCollectionStatus,
)
from app.models.script import Script, ScriptStatus, ScriptTone
from app.providers.media import LocalMediaProvider
from app.schemas.broll import (
    BrollAssetResponse,
    BrollCollectionResponse,
    BrollCollectionStatusResponse,
)
from app.services.broll_retrieval_service import BrollRetrievalService
from app.tasks import broll_retrieval as broll_task


class WorkflowState:
    def __init__(self) -> None:
        self.script = Script(
            id=4,
            video_id="video-1",
            video_analysis_id=7,
            status=ScriptStatus.COMPLETED,
            title="Purposeful editing",
            hook="Start with your strongest visual.",
            body="Use deliberate motion and clean composition.",
            full_script=(
                "Start with your strongest visual. Use deliberate motion and clean composition."
            ),
            estimated_duration_seconds=10,
            target_duration_seconds=30,
            tone=ScriptTone.EDUCATIONAL,
            language="en",
            generation_options={},
            sections=[
                {"order": 0, "type": "hook", "text": "Start with your strongest visual."},
                {
                    "order": 1,
                    "type": "body",
                    "text": "Use deliberate motion and clean composition.",
                },
            ],
            completed_at=datetime.now(UTC),
        )
        self.collections: dict[int, BrollCollection] = {}
        self.assets: dict[int, BrollAsset] = {}
        self.next_collection_id = 1
        self.next_asset_id = 1
        self.events: list[str] = []


class InMemorySession:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closes += 1

    async def commit(self) -> None:
        self.commits += 1
        self.state.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.state.events.append("rollback")


class InMemoryScriptRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, script_id: int) -> Script | None:
        return self.state.script if self.state.script.id == script_id else None


class InMemoryCollectionRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.state = session.state

    async def create(self, collection: BrollCollection) -> BrollCollection:
        collection.id = self.state.next_collection_id
        self.state.next_collection_id += 1
        timestamp = datetime.now(UTC)
        collection.created_at = timestamp
        collection.updated_at = timestamp
        self.state.collections[collection.id] = collection
        self.state.events.append(f"create_collection:{collection.id}")
        return collection

    async def get(self, collection_id: int) -> BrollCollection | None:
        return self.state.collections.get(collection_id)

    async def get_by_script_id(self, script_id: int) -> list[BrollCollection]:
        rows = [row for row in self.state.collections.values() if row.script_id == script_id]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    async def save(self, collection: BrollCollection) -> BrollCollection:
        collection.updated_at = datetime.now(UTC)
        self.state.collections[collection.id] = collection
        self.state.events.append(f"save_collection:{collection.id}:{collection.status.value}")
        return collection

    async def commit(self) -> None:
        await self.session.commit()


class InMemoryAssetRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.state = session.state

    async def create(self, asset: BrollAsset) -> BrollAsset:
        asset.id = self.state.next_asset_id
        self.state.next_asset_id += 1
        timestamp = datetime.now(UTC)
        asset.created_at = timestamp
        asset.updated_at = timestamp
        self.state.assets[asset.id] = asset
        self.state.events.append(f"create_asset:{asset.id}")
        return asset

    async def get(self, asset_id: int) -> BrollAsset | None:
        return self.state.assets.get(asset_id)

    async def get_by_collection_id(self, collection_id: int) -> list[BrollAsset]:
        rows = [row for row in self.state.assets.values() if row.collection_id == collection_id]
        return sorted(
            rows,
            key=lambda row: (
                row.script_section_order is None,
                row.script_section_order if row.script_section_order is not None else 0,
                -(row.relevance_score if row.relevance_score is not None else -1),
                row.id,
            ),
        )

    async def save(self, asset: BrollAsset) -> BrollAsset:
        asset.updated_at = datetime.now(UTC)
        self.state.assets[asset.id] = asset
        self.state.events.append(f"save_asset:{asset.id}:{asset.status.value}")
        return asset

    async def commit(self) -> None:
        await self.session.commit()


class CountingLocalMediaProvider(LocalMediaProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, search_input):
        self.calls += 1
        return await super().search(search_input)


class FailingMediaProvider:
    async def search(self, search_input):
        raise RuntimeError("local media search failed")


class EmptyMediaProvider:
    async def search(self, search_input):
        return []


class Phase6Harness:
    def __init__(self, monkeypatch) -> None:
        self.state = WorkflowState()
        self.session = InMemorySession(self.state)
        self.provider = CountingLocalMediaProvider()
        self.queued: list[int] = []
        self.publication_commits: list[int] = []
        self.script_repository = InMemoryScriptRepository(self.session)
        self.collection_repository = InMemoryCollectionRepository(self.session)
        self.asset_repository = InMemoryAssetRepository(self.session)
        self.service = self._service(self.provider)

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_broll_retrieval_service] = lambda: self.service

        def publish(collection_id: int) -> None:
            assert self.state.events[-1] == "commit"
            self.publication_commits.append(self.session.commits)
            self.queued.append(collection_id)

        monkeypatch.setattr(broll_endpoint.retrieve_broll, "delay", publish)
        monkeypatch.setattr(broll_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(broll_task, "ScriptRepository", InMemoryScriptRepository)
        monkeypatch.setattr(broll_task, "BrollCollectionRepository", InMemoryCollectionRepository)
        monkeypatch.setattr(broll_task, "BrollAssetRepository", InMemoryAssetRepository)
        monkeypatch.setattr(broll_task, "LocalMediaProvider", lambda: self.provider)

    def _service(self, provider) -> BrollRetrievalService:
        return BrollRetrievalService(
            self.script_repository,
            self.collection_repository,
            self.asset_repository,
            provider,
        )

    def client(self) -> TestClient:
        return TestClient(self.app)

    def run_task(self, collection_id: int) -> dict[str, int | str]:
        return asyncio.run(broll_task._run_broll_retrieval(collection_id))


def request_body(**option_changes) -> dict:
    options = {
        "provider": "local",
        "media_type": "video",
        "orientation": "portrait",
        "max_assets_per_section": 2,
        "min_duration_seconds": 2,
        "max_duration_seconds": 8,
        "min_width": 720,
        "min_height": 1280,
        "safe_search": True,
        "download_assets": False,
        "language": "en",
    }
    options.update(option_changes)
    return {"script_id": 4, "query_strategy": "section_keywords", "options": options}


def test_phase4_to_phase6_workflow_variants_idempotency_and_asset_updates(monkeypatch) -> None:
    harness = Phase6Harness(monkeypatch)

    with harness.client() as client:
        created_response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())
        pending = BrollCollectionStatusResponse.model_validate(created_response.json())
        assert created_response.status_code == 202
        assert pending.status == BrollCollectionStatus.PENDING
        assert harness.queued == [pending.id]
        assert harness.publication_commits == [1]

        task_result = harness.run_task(harness.queued.pop())
        assert task_result["collection_status"] == "completed"
        assert task_result["asset_count"] == 4
        first_assets = awaitable_assets(harness, pending.id)
        assert len(first_assets) == 4
        assert all(item.status == BrollAssetStatus.CANDIDATE for item in first_assets)
        assert all(item.provider.value == "local" for item in first_assets)
        assert all(item.external_id.startswith("local-") for item in first_assets)
        assert all(item.source_url.startswith("https://local.invalid/") for item in first_assets)

        completed_response = client.get(f"/api/v1/broll-collections/{pending.id}")
        completed = BrollCollectionResponse.model_validate(completed_response.json())
        assert completed_response.status_code == 200
        assert [item.id for item in completed.assets] == [item.id for item in first_assets]

        asset_list_response = client.get(f"/api/v1/broll-collections/{pending.id}/assets")
        listed_assets = [
            BrollAssetResponse.model_validate(row) for row in asset_list_response.json()
        ]
        assert [item.id for item in listed_assets] == [item.id for item in first_assets]

        first = first_assets[0]
        sibling = first_assets[1]
        fetched = client.get(f"/api/v1/broll-assets/{first.id}")
        BrollAssetResponse.model_validate(fetched.json())
        selected = client.post(f"/api/v1/broll-assets/{first.id}/select")
        assert (
            BrollAssetResponse.model_validate(selected.json()).status == BrollAssetStatus.SELECTED
        )
        assert sibling.status == BrollAssetStatus.CANDIDATE
        rejected = client.post(f"/api/v1/broll-assets/{first.id}/reject")
        assert (
            BrollAssetResponse.model_validate(rejected.json()).status == BrollAssetStatus.REJECTED
        )
        assert sibling.status == BrollAssetStatus.CANDIDATE
        assert first.storage_key is None

        second_response = client.post(
            "/api/v1/scripts/4/broll-collections",
            json=request_body(orientation="landscape", max_assets_per_section=1),
        )
        second = BrollCollectionStatusResponse.model_validate(second_response.json())
        assert second.id != pending.id
        assert len(harness.state.collections) == 2
        second_row = harness.state.collections[second.id]
        assert second_row.retrieval_options["orientation"] == "landscape"
        assert harness.state.collections[pending.id].retrieval_options["orientation"] == "portrait"
        harness.run_task(harness.queued.pop())
        second_assets = awaitable_assets(harness, second.id)
        assert len(second_assets) == 2
        assert not {item.id for item in first_assets} & {item.id for item in second_assets}

        calls_before_rerun = harness.provider.calls
        asset_count_before = len(harness.state.assets)
        harness.run_task(pending.id)
        assert harness.provider.calls == calls_before_rerun
        assert len(harness.state.assets) == asset_count_before

        queued_before_retry = list(harness.queued)
        retry_completed = client.post(f"/api/v1/broll-collections/{pending.id}/retry")
        assert retry_completed.status_code == 200
        BrollCollectionResponse.model_validate(retry_completed.json())
        assert harness.queued == queued_before_retry

        list_response = client.get("/api/v1/scripts/4/broll-collections")
        assert [item["id"] for item in list_response.json()] == [second.id, pending.id]
        assert all(item["status"] == "completed" for item in list_response.json())

        original_ids = {item.id for item in first_assets}
        harness.state.collections[pending.id].status = BrollCollectionStatus.FAILED
        retry_preserved = client.post(f"/api/v1/broll-collections/{pending.id}/retry")
        assert retry_preserved.status_code == 202
        harness.run_task(harness.queued.pop())
        assert {item.id for item in awaitable_assets(harness, pending.id)} == original_ids
        assert len(harness.state.collections) == 2


def test_retrieval_failure_get_retry_and_same_collection_recovery(monkeypatch) -> None:
    harness = Phase6Harness(monkeypatch)

    with harness.client() as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())
        collection_id = BrollCollectionStatusResponse.model_validate(response.json()).id
        harness.queued.clear()
        harness.provider = FailingMediaProvider()

        with pytest.raises(BrollRetrievalError):
            harness.run_task(collection_id)

        failed_row = harness.state.collections[collection_id]
        assert failed_row.status == BrollCollectionStatus.FAILED
        assert failed_row.completed_at is None
        assert failed_row.error_message == "local media search failed"
        assert harness.state.events[-1] == "commit"

        failed_response = client.get(f"/api/v1/broll-collections/{collection_id}")
        failed = BrollCollectionStatusResponse.model_validate(failed_response.json())
        assert failed.status == BrollCollectionStatus.FAILED
        assert failed.error_message == "local media search failed"

        commits_before_retry = harness.session.commits
        retry_response = client.post(f"/api/v1/broll-collections/{collection_id}/retry")
        retry = BrollCollectionStatusResponse.model_validate(retry_response.json())
        assert retry_response.status_code == 202
        assert retry.id == collection_id
        assert retry.status == BrollCollectionStatus.PENDING
        assert retry.error_message is None
        assert harness.publication_commits[-1] == commits_before_retry + 1

        harness.provider = CountingLocalMediaProvider()
        recovered = harness.run_task(harness.queued.pop())
        assert recovered["collection_status"] == "completed"
        assert recovered["asset_count"] == 4
        assert len(harness.state.collections) == 1


def test_no_results_is_nontransient_failed_state(monkeypatch) -> None:
    harness = Phase6Harness(monkeypatch)

    with harness.client() as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())
        collection_id = BrollCollectionStatusResponse.model_validate(response.json()).id
        harness.provider = EmptyMediaProvider()

        with pytest.raises(BrollRetrievalError):
            harness.run_task(harness.queued.pop())

        failed = harness.state.collections[collection_id]
        assert failed.status == BrollCollectionStatus.FAILED
        assert failed.completed_at is None
        assert failed.error_message == "No valid B-roll candidates were returned"
        assert not awaitable_assets(harness, collection_id)
        assert broll_task.retrieve_broll.autoretry_for != (BrollRetrievalError,)
        BrollCollectionStatusResponse.model_validate(
            client.get(f"/api/v1/broll-collections/{collection_id}").json()
        )


def test_broker_failure_persists_same_row_without_provider_execution(monkeypatch) -> None:
    harness = Phase6Harness(monkeypatch)

    def fail_publish(collection_id: int) -> None:
        assert harness.state.events[-1] == "commit"
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(broll_endpoint.retrieve_broll, "delay", fail_publish)
    with harness.client() as client:
        response = client.post("/api/v1/scripts/4/broll-collections", json=request_body())

    assert response.status_code == 503
    assert response.json() == {"detail": "B-roll retrieval could not be queued"}
    assert len(harness.state.collections) == 1
    failed = next(iter(harness.state.collections.values()))
    assert failed.status == BrollCollectionStatus.FAILED
    assert failed.error_message == "B-roll retrieval task enqueue failed: broker unavailable"
    assert not harness.state.assets
    assert harness.provider.calls == 0
    assert harness.state.events[-2:] == [
        f"save_collection:{failed.id}:failed",
        "commit",
    ]


def awaitable_assets(harness: Phase6Harness, collection_id: int) -> list[BrollAsset]:
    return asyncio.run(harness.asset_repository.get_by_collection_id(collection_id))
