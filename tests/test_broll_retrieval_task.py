from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.core.exceptions import BrollCollectionNotFoundError, BrollRetrievalError
from app.models.broll import BrollAsset, BrollCollection, BrollCollectionStatus
from app.services.broll_retrieval_service import BrollRetrievalService
from app.tasks import broll_retrieval as task_module


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_task_composes_dependencies_and_returns_service_result(monkeypatch) -> None:
    session = FakeSession()
    dependencies = {}
    script_repository = object()
    collection_repository = object()
    asset_repository = object()
    provider = object()

    class FakeService:
        def __init__(
            self,
            script_repository,
            collection_repository,
            asset_repository,
            media_provider,
        ) -> None:
            dependencies.update(
                script_repository=script_repository,
                collection_repository=collection_repository,
                asset_repository=asset_repository,
                media_provider=media_provider,
            )

        async def process_collection(self, collection_id: int):
            dependencies["collection_id"] = collection_id
            return (
                SimpleNamespace(id=collection_id, status=BrollCollectionStatus.COMPLETED),
                [object(), object(), object()],
            )

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: script_repository)
    monkeypatch.setattr(
        task_module, "BrollCollectionRepository", lambda received: collection_repository
    )
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: asset_repository)
    monkeypatch.setattr(task_module, "LocalMediaProvider", lambda: provider)
    monkeypatch.setattr(task_module, "BrollRetrievalService", FakeService)

    result = await task_module._run_broll_retrieval(9)

    assert result == {
        "collection_id": 9,
        "collection_status": "completed",
        "asset_count": 3,
    }
    assert dependencies == {
        "script_repository": script_repository,
        "collection_repository": collection_repository,
        "asset_repository": asset_repository,
        "media_provider": provider,
        "collection_id": 9,
    }
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_sync_entrypoint_runs_async_helper_and_is_registered(monkeypatch) -> None:
    async def fake_run(collection_id: int) -> dict[str, int | str]:
        return {
            "collection_id": collection_id,
            "collection_status": "completed",
            "asset_count": 2,
        }

    monkeypatch.setattr(task_module, "_run_broll_retrieval", fake_run)

    assert task_module.retrieve_broll.run(7) == {
        "collection_id": 7,
        "collection_status": "completed",
        "asset_count": 2,
    }
    assert task_module.retrieve_broll.name == "broll.retrieve"
    assert task_module.celery_app.tasks["broll.retrieve"].name == "broll.retrieve"
    assert "app.tasks.broll_retrieval" in task_module.celery_app.conf.include


async def test_retrieval_error_commits_failed_state_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_collection(self, collection_id: int):
            raise BrollRetrievalError

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(BrollRetrievalError):
        await task_module._run_broll_retrieval(7)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_precondition_error_rolls_back_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_collection(self, collection_id: int):
            raise BrollCollectionNotFoundError

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(BrollCollectionNotFoundError):
        await task_module._run_broll_retrieval(7)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


async def test_completed_collection_is_idempotent(monkeypatch) -> None:
    session = FakeSession()
    collection = BrollCollection(
        id=7,
        script_id=4,
        status=BrollCollectionStatus.COMPLETED,
        provider="local",
        query_strategy="section_keywords",
        retrieval_options={},
    )
    assets = [BrollAsset(id=11, collection_id=7), BrollAsset(id=12, collection_id=7)]

    class CompletedCollectionRepository:
        async def get(self, collection_id: int):
            return collection if collection_id == collection.id else None

    class ExistingAssetRepository:
        async def get_by_collection_id(self, collection_id: int):
            return assets

        async def create(self, asset):
            raise AssertionError("the task must not create or duplicate assets")

    class UnexpectedScriptRepository:
        async def get(self, script_id: int):
            raise AssertionError("completed collections must not reload their script")

    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def search(self, search_input):
            self.calls += 1
            raise AssertionError("completed collections must not search again")

    provider = CountingProvider()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        task_module, "ScriptRepository", lambda received: UnexpectedScriptRepository()
    )
    monkeypatch.setattr(
        task_module,
        "BrollCollectionRepository",
        lambda received: CompletedCollectionRepository(),
    )
    monkeypatch.setattr(
        task_module, "BrollAssetRepository", lambda received: ExistingAssetRepository()
    )
    monkeypatch.setattr(task_module, "LocalMediaProvider", lambda: provider)
    monkeypatch.setattr(task_module, "BrollRetrievalService", BrollRetrievalService)

    result = await task_module._run_broll_retrieval(7)

    assert result["asset_count"] == 2
    assert provider.calls == 0
    assert session.commits == 1
    assert session.closed


def test_operational_error_uses_bounded_celery_retry(monkeypatch) -> None:
    async def fail_with_operational_error(collection_id: int):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(task_module, "_run_broll_retrieval", fail_with_operational_error)
    monkeypatch.setattr(task_module.retrieve_broll, "retry", retry)

    with pytest.raises(Retry):
        task_module.retrieve_broll.run(7)

    retry.assert_called_once()
    assert task_module.retrieve_broll.autoretry_for == (OperationalError,)
    assert task_module.retrieve_broll.retry_backoff is True
    assert task_module.retrieve_broll.max_retries == 3


def patch_dependencies(monkeypatch, session: FakeSession, service_type: type) -> None:
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "BrollCollectionRepository", lambda received: object())
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalMediaProvider", lambda: object())
    monkeypatch.setattr(task_module, "BrollRetrievalService", service_type)
