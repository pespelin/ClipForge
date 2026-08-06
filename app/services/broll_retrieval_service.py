import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import (
    BrollAssetNotFoundError,
    BrollCollectionNotFoundError,
    BrollNoResultsError,
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
)
from app.models.script import Script, ScriptStatus
from app.providers.media import MediaProvider
from app.repositories.broll_repository import (
    BrollAssetRepository,
    BrollCollectionRepository,
)
from app.repositories.script_repository import ScriptRepository
from app.schemas.broll import (
    BrollRetrievalOptions,
    MediaCandidateResult,
    MediaSearchInput,
)
from app.schemas.script import ScriptSection

WORD_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
QUERY_WORD_LIMIT = 8
SUPPORTED_QUERY_STRATEGIES = {"section_keywords"}
QUERY_STOP_WORDS = {
    "and",
    "for",
    "from",
    "into",
    "that",
    "the",
    "this",
    "with",
    "your",
}


class BrollRetrievalService:
    def __init__(
        self,
        script_repository: ScriptRepository,
        collection_repository: BrollCollectionRepository,
        asset_repository: BrollAssetRepository,
        media_provider: MediaProvider,
    ) -> None:
        self.script_repository = script_repository
        self.collection_repository = collection_repository
        self.asset_repository = asset_repository
        self.media_provider = media_provider

    async def create_collection(
        self,
        script_id: int,
        options: BrollRetrievalOptions,
        *,
        query_strategy: str = "section_keywords",
    ) -> BrollCollection:
        validated_options = BrollRetrievalOptions.model_validate(options)
        script = await self._get_script(script_id)
        self._verify_script_ready(script)
        normalized_strategy = self._normalize(query_strategy).casefold()
        if not normalized_strategy:
            raise UnsupportedBrollQueryStrategyError
        return await self.collection_repository.create(
            BrollCollection(
                script_id=script_id,
                status=BrollCollectionStatus.PENDING,
                provider=validated_options.provider,
                query_strategy=normalized_strategy,
                retrieval_options=validated_options.model_dump(mode="json"),
            )
        )

    async def process_collection(
        self, collection_id: int
    ) -> tuple[BrollCollection, list[BrollAsset]]:
        collection = await self.get_collection(collection_id)
        if collection.status == BrollCollectionStatus.COMPLETED:
            assets = await self.asset_repository.get_by_collection_id(collection.id)
            return collection, assets

        script = await self._get_script(collection.script_id)
        self._verify_script_ready(script)
        collection.status = BrollCollectionStatus.SEARCHING
        collection.completed_at = None
        collection.error_message = None
        await self.collection_repository.save(collection)

        try:
            options = BrollRetrievalOptions.model_validate(collection.retrieval_options)
            search_inputs = self._build_search_inputs(collection, script, options)
            existing_assets = await self.asset_repository.get_by_collection_id(collection.id)
            seen_keys = {self._asset_duplicate_key(asset) for asset in existing_assets}
            persisted_assets = list(existing_assets)

            for search_input in search_inputs:
                raw_candidates = await self.media_provider.search(search_input)
                accepted_for_section = sum(
                    asset.script_section_order == search_input.section_order
                    for asset in existing_assets
                )
                if accepted_for_section >= options.max_assets_per_section:
                    continue
                for raw_candidate in raw_candidates:
                    candidate = MediaCandidateResult.model_validate(raw_candidate)
                    self._json_safe(candidate.metadata_data)
                    duplicate_key = self._candidate_duplicate_key(candidate)
                    if duplicate_key in seen_keys:
                        continue
                    asset = await self.asset_repository.create(
                        self._asset_from_candidate(collection.id, search_input, candidate)
                    )
                    seen_keys.add(duplicate_key)
                    persisted_assets.append(asset)
                    accepted_for_section += 1
                    if accepted_for_section >= options.max_assets_per_section:
                        break

            if not persisted_assets:
                raise BrollNoResultsError

            collection.status = BrollCollectionStatus.COMPLETED
            collection.completed_at = datetime.now(UTC)
            collection.error_message = None
            await self.collection_repository.save(collection)
            return collection, persisted_assets
        except Exception as error:
            collection.status = BrollCollectionStatus.FAILED
            collection.completed_at = None
            collection.error_message = self._error_message(error)
            await self.collection_repository.save(collection)
            raise BrollRetrievalError from error

    async def get_collection(self, collection_id: int) -> BrollCollection:
        collection = await self.collection_repository.get(collection_id)
        if collection is None:
            raise BrollCollectionNotFoundError
        return collection

    async def list_collections_for_script(self, script_id: int) -> list[BrollCollection]:
        await self._get_script(script_id)
        return await self.collection_repository.get_by_script_id(script_id)

    async def list_assets_for_collection(self, collection_id: int) -> list[BrollAsset]:
        await self.get_collection(collection_id)
        return await self.asset_repository.get_by_collection_id(collection_id)

    async def select_asset(self, asset_id: int) -> BrollAsset:
        return await self._set_asset_status(asset_id, BrollAssetStatus.SELECTED)

    async def reject_asset(self, asset_id: int) -> BrollAsset:
        return await self._set_asset_status(asset_id, BrollAssetStatus.REJECTED)

    async def _set_asset_status(self, asset_id: int, status: BrollAssetStatus) -> BrollAsset:
        asset = await self.asset_repository.get(asset_id)
        if asset is None:
            raise BrollAssetNotFoundError
        await self.get_collection(asset.collection_id)
        asset.status = status
        asset.error_message = None
        return await self.asset_repository.save(asset)

    async def _get_script(self, script_id: int) -> Script:
        script = await self.script_repository.get(script_id)
        if script is None:
            raise ScriptNotFoundError
        return script

    @classmethod
    def _verify_script_ready(cls, script: Script) -> None:
        if script.status != ScriptStatus.COMPLETED:
            raise BrollScriptNotReadyError
        if cls._usable_sections(script):
            return
        fallback = cls._fallback_text(script)
        if not cls._is_meaningful(fallback):
            raise BrollUnusableScriptError

    @classmethod
    def _build_search_inputs(
        cls,
        collection: BrollCollection,
        script: Script,
        options: BrollRetrievalOptions,
    ) -> list[MediaSearchInput]:
        if collection.query_strategy not in SUPPORTED_QUERY_STRATEGIES:
            raise UnsupportedBrollQueryStrategyError
        sections = cls._usable_sections(script)
        if not sections:
            fallback = cls._fallback_text(script)
            sections = [
                ScriptSection(
                    order=0,
                    type="full_script",
                    text=fallback,
                )
            ]
            source_orders: list[int | None] = [None]
        else:
            source_orders = [section.order for section in sections]

        search_inputs = []
        for section, source_order in zip(sections, source_orders, strict=True):
            query = cls._section_keywords(section.text, script.title)
            if not cls._is_meaningful(query):
                continue
            search_inputs.append(
                MediaSearchInput(
                    collection_id=collection.id,
                    script_id=script.id,
                    provider=options.provider,
                    section_order=source_order,
                    section_type=section.type,
                    section_text=section.text,
                    query=query,
                    language=options.language,
                    media_type=options.media_type,
                    orientation=options.orientation,
                    min_duration_seconds=options.min_duration_seconds,
                    max_duration_seconds=options.max_duration_seconds,
                    min_width=options.min_width,
                    min_height=options.min_height,
                    safe_search=options.safe_search,
                    max_results=options.max_assets_per_section,
                )
            )
        if not search_inputs:
            raise BrollUnusableScriptError
        return search_inputs

    @classmethod
    def _usable_sections(cls, script: Script) -> list[ScriptSection]:
        sections = []
        for raw_section in script.sections or []:
            try:
                section = ScriptSection.model_validate(raw_section)
            except ValidationError:
                continue
            if cls._is_meaningful(section.text):
                sections.append(section)
        return sections

    @classmethod
    def _fallback_text(cls, script: Script) -> str:
        return cls._normalize(
            " ".join(
                value
                for value in (script.title, script.hook, script.body, script.full_script)
                if value and cls._is_meaningful(value)
            )
        )

    @classmethod
    def _section_keywords(cls, section_text: str, title: str | None) -> str:
        tokens = WORD_PATTERN.findall(cls._normalize(f"{section_text} {title or ''}").casefold())
        filtered = [token for token in tokens if token not in QUERY_STOP_WORDS]
        chosen = filtered or tokens
        unique = list(dict.fromkeys(chosen))
        return " ".join(unique[:QUERY_WORD_LIMIT])

    @staticmethod
    def _asset_from_candidate(
        collection_id: int,
        search_input: MediaSearchInput,
        candidate: MediaCandidateResult,
    ) -> BrollAsset:
        metadata = BrollRetrievalService._json_safe(candidate.metadata_data)
        return BrollAsset(
            collection_id=collection_id,
            script_section_order=search_input.section_order,
            provider=candidate.provider,
            external_id=candidate.external_id,
            media_type=candidate.media_type,
            status=BrollAssetStatus.CANDIDATE,
            query=search_input.query,
            title=candidate.title,
            description=candidate.description,
            source_url=str(candidate.source_url) if candidate.source_url else None,
            preview_url=str(candidate.preview_url) if candidate.preview_url else None,
            download_url=str(candidate.download_url) if candidate.download_url else None,
            storage_key=None,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
            file_size_bytes=None,
            mime_type=candidate.mime_type,
            checksum=None,
            attribution=candidate.attribution,
            license_name=candidate.license_name,
            photographer_or_creator=candidate.photographer_or_creator,
            orientation=candidate.orientation,
            relevance_score=candidate.relevance_score,
            metadata_data=metadata,
            downloaded_at=None,
            error_message=None,
        )

    @classmethod
    def _candidate_duplicate_key(cls, candidate: MediaCandidateResult) -> str:
        return cls._duplicate_key(
            provider=candidate.provider.value,
            external_id=candidate.external_id,
            source_url=str(candidate.source_url) if candidate.source_url else None,
            fallback=candidate.model_dump(mode="json"),
        )

    @classmethod
    def _asset_duplicate_key(cls, asset: BrollAsset) -> str:
        fallback = {
            "provider": asset.provider.value,
            "external_id": asset.external_id,
            "media_type": asset.media_type.value,
            "title": asset.title,
            "description": asset.description,
            "source_url": asset.source_url,
            "preview_url": asset.preview_url,
            "download_url": asset.download_url,
            "width": asset.width,
            "height": asset.height,
            "duration_seconds": asset.duration_seconds,
            "mime_type": asset.mime_type,
            "attribution": asset.attribution,
            "license_name": asset.license_name,
            "photographer_or_creator": asset.photographer_or_creator,
            "orientation": asset.orientation.value,
            "relevance_score": asset.relevance_score,
            "metadata_data": asset.metadata_data,
        }
        return cls._duplicate_key(
            provider=asset.provider.value,
            external_id=asset.external_id,
            source_url=asset.source_url,
            fallback=fallback,
        )

    @staticmethod
    def _duplicate_key(
        *, provider: str, external_id: str | None, source_url: str | None, fallback: dict
    ) -> str:
        provider_key = provider.casefold()
        if external_id:
            return f"external:{provider_key}:{external_id.strip().casefold()}"
        if source_url:
            return f"source:{provider_key}:{source_url.strip().casefold()}"
        serialized = json.dumps(fallback, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"hash:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.split())

    @classmethod
    def _is_meaningful(cls, value: str) -> bool:
        return any(character.isalnum() for character in cls._normalize(value))

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "Media provider returned an invalid structured candidate"
        if isinstance(error, UnsupportedBrollQueryStrategyError):
            return "Unsupported B-roll query strategy"
        if isinstance(error, BrollNoResultsError):
            return "No valid B-roll candidates were returned"
        return str(error).strip() or type(error).__name__
