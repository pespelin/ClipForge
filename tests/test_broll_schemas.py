from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.broll import (
    BrollAssetStatus,
    BrollCollectionStatus,
    BrollMediaType,
    BrollOrientation,
    BrollProvider,
)
from app.schemas.broll import (
    BrollAssetCandidate,
    BrollAssetResponse,
    BrollAssetStatusResponse,
    BrollCollectionResponse,
    BrollRetrievalOptions,
    BrollRetrievalRequest,
)


def candidate_data(**values) -> dict:
    defaults = {
        "script_section_order": 0,
        "provider": "local",
        "external_id": "asset-1",
        "media_type": "video",
        "query": "city skyline",
        "source_url": "https://example.com/source/1",
        "preview_url": "https://example.com/preview/1",
        "download_url": "https://example.com/download/1",
        "width": 1080,
        "height": 1920,
        "duration_seconds": 8,
        "file_size_bytes": 2048,
        "mime_type": "video/mp4",
        "orientation": "portrait",
        "relevance_score": 0.9,
        "metadata_data": {"source_rank": 1},
    }
    defaults.update(values)
    return defaults


def asset_response_data(**values) -> dict:
    timestamp = datetime.now(UTC)
    defaults = candidate_data(
        id=1,
        collection_id=2,
        status=BrollAssetStatus.CANDIDATE,
        storage_key=None,
        checksum=None,
        attribution=None,
        license_name=None,
        photographer_or_creator=None,
        created_at=timestamp,
        updated_at=timestamp,
        downloaded_at=None,
        error_message=None,
    )
    defaults.update(values)
    return defaults


def test_retrieval_options_have_provider_neutral_defaults() -> None:
    result = BrollRetrievalOptions()

    assert result.provider is BrollProvider.LOCAL
    assert result.media_type is BrollMediaType.VIDEO
    assert result.orientation is BrollOrientation.PORTRAIT
    assert result.max_assets_per_section == 3
    assert result.min_duration_seconds == 0
    assert result.max_duration_seconds == 60
    assert result.min_width == 720
    assert result.min_height == 1280
    assert result.safe_search is True
    assert result.download_assets is False
    assert result.language == "en"


def test_retrieval_request_defaults_and_normalizes_strategy() -> None:
    result = BrollRetrievalRequest(script_id=4, query_strategy="  section_keywords  ")

    assert result.query_strategy == "section_keywords"
    assert result.options == BrollRetrievalOptions()


@pytest.mark.parametrize("value", [0, 21])
def test_max_assets_per_section_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        BrollRetrievalOptions(max_assets_per_section=value)


@pytest.mark.parametrize(
    "values",
    [
        {"min_duration_seconds": -1},
        {"max_duration_seconds": -1},
        {"min_duration_seconds": 10, "max_duration_seconds": 9},
    ],
)
def test_duration_range_validation(values: dict) -> None:
    with pytest.raises(ValidationError):
        BrollRetrievalOptions.model_validate(values)


def test_equal_duration_bounds_are_valid() -> None:
    result = BrollRetrievalOptions(min_duration_seconds=5, max_duration_seconds=5)

    assert result.min_duration_seconds == result.max_duration_seconds == 5


@pytest.mark.parametrize(("field", "value"), [("min_width", 0), ("min_height", -1)])
def test_minimum_dimensions_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        BrollRetrievalOptions(**{field: value})


@pytest.mark.parametrize("language", ["EN", "english", "en-us", "en_US"])
def test_language_uses_existing_convention(language: str) -> None:
    with pytest.raises(ValidationError):
        BrollRetrievalOptions(language=language)


def test_asset_candidate_normalizes_query_and_identifiers() -> None:
    candidate = BrollAssetCandidate.model_validate(
        candidate_data(query="  city skyline  ", external_id="  asset-1  ")
    )

    assert candidate.query == "city skyline"
    assert candidate.external_id == "asset-1"
    assert candidate.metadata_data == {"source_rank": 1}


@pytest.mark.parametrize(
    "updates",
    [
        {"query": "   "},
        {"external_id": "   "},
        {"provider": "   "},
        {"script_section_order": -1},
        {"width": 0},
        {"height": -1},
        {"duration_seconds": -1},
        {"file_size_bytes": -1},
        {"relevance_score": 1.01},
    ],
)
def test_asset_candidate_rejects_invalid_values(updates: dict) -> None:
    with pytest.raises(ValidationError):
        BrollAssetCandidate.model_validate(candidate_data(**updates))


@pytest.mark.parametrize("field", ["source_url", "preview_url", "download_url"])
def test_asset_candidate_validates_optional_urls(field: str) -> None:
    with pytest.raises(ValidationError):
        BrollAssetCandidate.model_validate(candidate_data(**{field: "not a URL"}))
    with pytest.raises(ValidationError):
        BrollAssetCandidate.model_validate(candidate_data(**{field: "   "}))


def test_asset_status_response_validates_from_attributes() -> None:
    result = BrollAssetStatusResponse.model_validate(asset_response_data())

    assert result.status is BrollAssetStatus.CANDIDATE
    assert result.collection_id == 2


def test_downloaded_asset_requires_non_empty_storage_key() -> None:
    with pytest.raises(ValidationError):
        BrollAssetResponse.model_validate(
            asset_response_data(status=BrollAssetStatus.DOWNLOADED, storage_key=None)
        )
    with pytest.raises(ValidationError):
        BrollAssetResponse.model_validate(
            asset_response_data(status=BrollAssetStatus.DOWNLOADED, storage_key="   ")
        )


def test_downloaded_asset_accepts_artifact_metadata() -> None:
    result = BrollAssetResponse.model_validate(
        asset_response_data(
            status=BrollAssetStatus.DOWNLOADED,
            storage_key="broll/2/1.mp4",
            checksum="sha256:abc",
            downloaded_at=datetime.now(UTC),
        )
    )

    assert result.storage_key == "broll/2/1.mp4"
    assert result.downloaded_at is not None


def test_collection_response_validates_options_assets_and_json() -> None:
    timestamp = datetime.now(UTC)
    result = BrollCollectionResponse.model_validate(
        {
            "id": 2,
            "script_id": 4,
            "status": BrollCollectionStatus.COMPLETED,
            "provider": BrollProvider.LOCAL,
            "query_strategy": "section_keywords",
            "retrieval_options": BrollRetrievalOptions().model_dump(mode="json"),
            "assets": [asset_response_data()],
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": timestamp,
            "error_message": None,
        }
    )

    assert result.assets[0].query == "city skyline"
    assert result.retrieval_options.max_assets_per_section == 3
