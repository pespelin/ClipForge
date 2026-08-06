from typing import Any

import pytest

from app.core.exceptions import (
    BrollAssetNotFoundError,
    BrollCollectionNotFoundError,
    BrollRetrievalError,
    BrollScriptNotReadyError,
    BrollUnusableScriptError,
    ScriptNotFoundError,
    UnsupportedBrollQueryStrategyError,
)
from app.models.broll import (
    BrollAsset,
    BrollAssetStatus,
    BrollCollection,
    BrollCollectionStatus,
    BrollMediaType,
    BrollOrientation,
    BrollProvider,
)
from app.models.script import Script, ScriptStatus, ScriptTone
from app.schemas.broll import (
    BrollRetrievalOptions,
    MediaCandidateResult,
    MediaSearchInput,
)
from app.services.broll_retrieval_service import BrollRetrievalService


class FakeScriptRepository:
    def __init__(self, script: Script | None) -> None:
        self.script = script

    async def get(self, script_id: int) -> Script | None:
        if self.script is not None and self.script.id == script_id:
            return self.script
        return None


class FakeCollectionRepository:
    def __init__(self) -> None:
        self.rows: list[BrollCollection] = []
        self.saved_statuses: list[BrollCollectionStatus] = []
        self.commits = 0

    async def create(self, collection: BrollCollection) -> BrollCollection:
        collection.id = len(self.rows) + 1
        self.rows.append(collection)
        return collection

    async def get(self, collection_id: int) -> BrollCollection | None:
        return next((row for row in self.rows if row.id == collection_id), None)

    async def get_by_script_id(self, script_id: int) -> list[BrollCollection]:
        return [row for row in self.rows if row.script_id == script_id]

    async def save(self, collection: BrollCollection) -> BrollCollection:
        self.saved_statuses.append(collection.status)
        return collection

    async def commit(self) -> None:
        self.commits += 1


class FakeAssetRepository:
    def __init__(self) -> None:
        self.rows: list[BrollAsset] = []
        self.saved_statuses: list[BrollAssetStatus] = []
        self.commits = 0

    async def create(self, asset: BrollAsset) -> BrollAsset:
        asset.id = len(self.rows) + 1
        self.rows.append(asset)
        return asset

    async def get(self, asset_id: int) -> BrollAsset | None:
        return next((row for row in self.rows if row.id == asset_id), None)

    async def get_by_collection_id(self, collection_id: int) -> list[BrollAsset]:
        return [row for row in self.rows if row.collection_id == collection_id]

    async def save(self, asset: BrollAsset) -> BrollAsset:
        self.saved_statuses.append(asset.status)
        return asset

    async def commit(self) -> None:
        self.commits += 1


class FakeMediaProvider:
    def __init__(self, results: list[MediaCandidateResult | dict[str, Any]]) -> None:
        self.results = results
        self.calls = 0
        self.inputs: list[MediaSearchInput] = []

    async def search(self, search_input: MediaSearchInput):
        self.calls += 1
        self.inputs.append(search_input)
        return self.results


class FailingMediaProvider:
    async def search(self, search_input: MediaSearchInput):
        raise RuntimeError("media provider unavailable")


def completed_script(
    *,
    status: ScriptStatus = ScriptStatus.COMPLETED,
    sections: list[dict] | None = None,
    full_script: str | None = None,
) -> Script:
    return Script(
        id=4,
        video_id="video-1",
        video_analysis_id=7,
        status=status,
        title="Editing Lessons",
        hook="Start with your strongest visual.",
        body="Remove every unnecessary pause.",
        full_script=(
            "Start with your strongest visual. Remove every unnecessary pause."
            if full_script is None
            else full_script
        ),
        target_duration_seconds=30,
        tone=ScriptTone.EDUCATIONAL,
        language="en",
        generation_options={"target_duration_seconds": 30},
        sections=(
            [
                {"order": 0, "type": "hook", "text": "Start with your strongest visual."},
                {"order": 1, "type": "body", "text": "Remove every unnecessary pause."},
            ]
            if sections is None
            else sections
        ),
    )


def retrieval_options(**values) -> BrollRetrievalOptions:
    defaults = {
        "provider": BrollProvider.LOCAL,
        "media_type": BrollMediaType.VIDEO,
        "orientation": BrollOrientation.PORTRAIT,
        "max_assets_per_section": 3,
        "min_duration_seconds": 0,
        "max_duration_seconds": 30,
        "min_width": 720,
        "min_height": 1280,
        "safe_search": True,
        "download_assets": False,
        "language": "en",
    }
    defaults.update(values)
    return BrollRetrievalOptions.model_validate(defaults)


def candidate(**values) -> dict[str, Any]:
    defaults = {
        "provider": "local",
        "external_id": "asset-1",
        "media_type": "video",
        "title": "Editing workspace",
        "description": "An editor working on a timeline",
        "source_url": "https://example.com/assets/1",
        "preview_url": "https://example.com/previews/1",
        "download_url": "https://example.com/downloads/1",
        "width": 1080,
        "height": 1920,
        "duration_seconds": 8,
        "mime_type": "video/mp4",
        "attribution": "Example source",
        "license_name": "Example license",
        "photographer_or_creator": "Creator",
        "orientation": "portrait",
        "relevance_score": 0.9,
        "metadata_data": {"rank": 1},
    }
    defaults.update(values)
    return defaults


def make_service(
    *,
    script: Script | None = None,
    collections: FakeCollectionRepository | None = None,
    assets: FakeAssetRepository | None = None,
    provider=None,
) -> tuple[BrollRetrievalService, FakeCollectionRepository, FakeAssetRepository]:
    collection_repository = collections or FakeCollectionRepository()
    asset_repository = assets or FakeAssetRepository()
    service = BrollRetrievalService(
        FakeScriptRepository(script),
        collection_repository,
        asset_repository,
        provider or FakeMediaProvider([candidate()]),
    )
    return service, collection_repository, asset_repository


async def test_create_collection_persists_pending_variant_and_options_snapshot() -> None:
    service, collections, _ = make_service(script=completed_script())
    options = retrieval_options(max_assets_per_section=2)

    collection = await service.create_collection(4, options)

    assert collection.status == BrollCollectionStatus.PENDING
    assert collection.script_id == 4
    assert collection.provider is BrollProvider.LOCAL
    assert collection.query_strategy == "section_keywords"
    assert collection.retrieval_options == options.model_dump(mode="json")
    assert collections.rows == [collection]

    options.max_assets_per_section = 10
    assert collection.retrieval_options["max_assets_per_section"] == 2


async def test_create_collection_allows_multiple_variants() -> None:
    service, collections, _ = make_service(script=completed_script())

    first = await service.create_collection(4, retrieval_options())
    second = await service.create_collection(
        4,
        retrieval_options(media_type=BrollMediaType.IMAGE),
    )

    assert first.id != second.id
    assert first.script_id == second.script_id == 4
    assert collections.rows == [first, second]


async def test_create_collection_rejects_missing_script() -> None:
    service, collections, _ = make_service()

    with pytest.raises(ScriptNotFoundError):
        await service.create_collection(4, retrieval_options())

    assert collections.rows == []


@pytest.mark.parametrize(
    "status", [ScriptStatus.PENDING, ScriptStatus.GENERATING, ScriptStatus.FAILED]
)
async def test_create_collection_requires_completed_script(status: ScriptStatus) -> None:
    service, collections, _ = make_service(script=completed_script(status=status))

    with pytest.raises(BrollScriptNotReadyError):
        await service.create_collection(4, retrieval_options())

    assert collections.rows == []


async def test_create_collection_rejects_unusable_script_content() -> None:
    script = completed_script(sections=[], full_script="... !!!")
    script.title = "..."
    script.hook = "!!!"
    script.body = "---"
    service, collections, _ = make_service(script=script)

    with pytest.raises(BrollUnusableScriptError):
        await service.create_collection(4, retrieval_options())

    assert collections.rows == []


async def test_process_generates_deterministic_section_queries_and_maps_candidates() -> None:
    provider = FakeMediaProvider([candidate()])
    service, collections, assets = make_service(script=completed_script(), provider=provider)
    collection = await service.create_collection(4, retrieval_options())

    result, persisted = await service.process_collection(collection.id)

    assert result.status == BrollCollectionStatus.COMPLETED
    assert result.completed_at is not None
    assert result.error_message is None
    assert collections.saved_statuses == [
        BrollCollectionStatus.SEARCHING,
        BrollCollectionStatus.COMPLETED,
    ]
    assert [search.query for search in provider.inputs] == [
        "start strongest visual editing lessons",
        "remove every unnecessary pause editing lessons",
    ]
    assert all(isinstance(search, MediaSearchInput) for search in provider.inputs)
    assert all(not isinstance(search, (Script, BrollCollection)) for search in provider.inputs)
    assert len(persisted) == 1
    asset = persisted[0]
    assert asset is assets.rows[0]
    assert asset.script_section_order == 0
    assert asset.status == BrollAssetStatus.CANDIDATE
    assert asset.storage_key is None
    assert asset.file_size_bytes is None
    assert asset.checksum is None
    assert asset.downloaded_at is None
    assert asset.metadata_data == {"rank": 1}


async def test_process_uses_fallback_query_without_usable_sections() -> None:
    provider = FakeMediaProvider([candidate()])
    script = completed_script(sections=[])
    service, _, _ = make_service(script=script, provider=provider)
    collection = await service.create_collection(4, retrieval_options())

    await service.process_collection(collection.id)

    assert len(provider.inputs) == 1
    search_input = provider.inputs[0]
    assert search_input.section_order is None
    assert search_input.section_type == "full_script"
    assert search_input.query == "editing lessons start strongest visual remove every unnecessary"


async def test_unsupported_query_strategy_persists_failed_collection() -> None:
    service, collections, _ = make_service(script=completed_script())
    collection = await service.create_collection(
        4, retrieval_options(), query_strategy="visual_semantics"
    )

    with pytest.raises(BrollRetrievalError) as error_info:
        await service.process_collection(collection.id)

    assert isinstance(error_info.value.__cause__, UnsupportedBrollQueryStrategyError)
    assert collection.status == BrollCollectionStatus.FAILED
    assert collection.completed_at is None
    assert collection.error_message == "Unsupported B-roll query strategy"
    assert collections.saved_statuses[-1] == BrollCollectionStatus.FAILED


async def test_max_assets_per_section_and_duplicate_suppression() -> None:
    provider = FakeMediaProvider(
        [
            candidate(external_id="one", source_url=None),
            candidate(external_id="one", source_url=None),
            candidate(external_id="two", source_url=None),
            candidate(external_id="three", source_url=None),
        ]
    )
    script = completed_script(
        sections=[{"order": 0, "type": "hook", "text": "Start with your strongest visual."}]
    )
    service, _, assets = make_service(script=script, provider=provider)
    collection = await service.create_collection(4, retrieval_options(max_assets_per_section=2))

    _, persisted = await service.process_collection(collection.id)

    assert len(persisted) == 2
    assert [asset.external_id for asset in assets.rows] == ["one", "two"]
    assert all(asset.script_section_order == 0 for asset in assets.rows)


async def test_completed_collection_is_idempotent() -> None:
    provider = FakeMediaProvider([candidate()])
    service, _, assets = make_service(script=completed_script(), provider=provider)
    collection = await service.create_collection(4, retrieval_options())
    await service.process_collection(collection.id)
    calls = provider.calls

    result, existing = await service.process_collection(collection.id)

    assert result is collection
    assert existing == assets.rows
    assert provider.calls == calls
    assert len(assets.rows) == 1


async def test_retry_does_not_duplicate_existing_candidates() -> None:
    provider = FakeMediaProvider([candidate()])
    service, _, assets = make_service(script=completed_script(), provider=provider)
    collection = await service.create_collection(4, retrieval_options())
    await service.process_collection(collection.id)
    collection.status = BrollCollectionStatus.FAILED

    result, persisted = await service.process_collection(collection.id)

    assert result.status == BrollCollectionStatus.COMPLETED
    assert persisted == assets.rows
    assert len(assets.rows) == 1


async def test_provider_exception_persists_failed_collection() -> None:
    service, collections, _ = make_service(
        script=completed_script(), provider=FailingMediaProvider()
    )
    collection = await service.create_collection(4, retrieval_options())

    with pytest.raises(BrollRetrievalError) as error_info:
        await service.process_collection(collection.id)

    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert collection.status == BrollCollectionStatus.FAILED
    assert collection.error_message == "media provider unavailable"
    assert collections.saved_statuses[-1] == BrollCollectionStatus.FAILED


async def test_invalid_provider_result_persists_failed_collection() -> None:
    provider = FakeMediaProvider([candidate(width=0)])
    service, collections, assets = make_service(script=completed_script(), provider=provider)
    collection = await service.create_collection(4, retrieval_options())

    with pytest.raises(BrollRetrievalError):
        await service.process_collection(collection.id)

    assert collection.status == BrollCollectionStatus.FAILED
    assert collection.error_message == "Media provider returned an invalid structured candidate"
    assert collections.saved_statuses[-1] == BrollCollectionStatus.FAILED
    assert assets.rows == []


async def test_zero_results_is_an_explicit_failed_retrieval() -> None:
    service, collections, _ = make_service(
        script=completed_script(), provider=FakeMediaProvider([])
    )
    collection = await service.create_collection(4, retrieval_options())

    with pytest.raises(BrollRetrievalError) as error_info:
        await service.process_collection(collection.id)

    assert collection.status == BrollCollectionStatus.FAILED
    assert collection.error_message == "No valid B-roll candidates were returned"
    assert error_info.value.__cause__ is not None
    assert collections.saved_statuses[-1] == BrollCollectionStatus.FAILED


async def test_get_and_list_collection_and_assets_behavior() -> None:
    service, collections, assets = make_service(script=completed_script())
    collection = await service.create_collection(4, retrieval_options())
    asset = await assets.create(
        BrollAsset(
            collection_id=collection.id,
            provider=BrollProvider.LOCAL,
            media_type=BrollMediaType.VIDEO,
            query="editing",
        )
    )

    assert await service.get_collection(collection.id) is collection
    assert await service.list_collections_for_script(4) == collections.rows
    assert await service.list_assets_for_collection(collection.id) == [asset]
    with pytest.raises(BrollCollectionNotFoundError):
        await service.get_collection(999)


async def test_select_and_reject_asset_are_persisted_without_download() -> None:
    service, _, assets = make_service(script=completed_script())
    collection = await service.create_collection(4, retrieval_options())
    asset = await assets.create(
        BrollAsset(
            collection_id=collection.id,
            provider=BrollProvider.LOCAL,
            media_type=BrollMediaType.VIDEO,
            query="editing",
        )
    )

    assert await service.select_asset(asset.id) is asset
    assert asset.status == BrollAssetStatus.SELECTED
    assert asset.storage_key is None
    assert await service.reject_asset(asset.id) is asset
    assert asset.status == BrollAssetStatus.REJECTED
    assert assets.saved_statuses == [BrollAssetStatus.SELECTED, BrollAssetStatus.REJECTED]
    with pytest.raises(BrollAssetNotFoundError):
        await service.select_asset(999)


async def test_api_transaction_methods_commit_and_reuse_existing_rows() -> None:
    service, collections, assets = make_service(script=completed_script())
    collection = await service.request_broll_retrieval(4, retrieval_options())

    assert collections.commits == 1
    collection.status = BrollCollectionStatus.FAILED
    collection.error_message = "old failure"
    retried, should_enqueue = await service.prepare_collection_retry(collection.id)
    assert retried is collection
    assert should_enqueue is True
    assert collection.status == BrollCollectionStatus.PENDING
    assert collection.error_message is None
    assert collections.commits == 2

    await service.mark_broll_enqueue_failed(collection, RuntimeError("broker unavailable"))
    assert collection.status == BrollCollectionStatus.FAILED
    assert collection.error_message == "B-roll retrieval task enqueue failed: broker unavailable"
    assert collections.commits == 3

    candidate = await assets.create(
        BrollAsset(
            collection_id=collection.id,
            provider=BrollProvider.LOCAL,
            media_type=BrollMediaType.VIDEO,
            query="editing",
        )
    )
    assert await service.get_asset(candidate.id) is candidate
    await service.select_asset_and_commit(candidate.id)
    await service.reject_asset_and_commit(candidate.id)
    assert assets.commits == 2
