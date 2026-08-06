import json

import pytest

from app.models.broll import BrollMediaType, BrollOrientation, BrollProvider
from app.providers.media import (
    LocalMediaProvider,
    UnsupportedMediaLanguageError,
    UnsupportedMediaProviderError,
    UnusableMediaSearchInputError,
)
from app.schemas.broll import MediaCandidateResult, MediaSearchInput


def search_input(**values) -> MediaSearchInput:
    defaults = {
        "collection_id": 7,
        "script_id": 4,
        "section_order": 2,
        "section_type": "body",
        "section_text": "A focused section about city architecture.",
        "query": "modern city architecture",
        "language": "en",
        "media_type": BrollMediaType.VIDEO,
        "orientation": BrollOrientation.PORTRAIT,
        "min_duration_seconds": 2.0,
        "max_duration_seconds": 12.0,
        "min_width": None,
        "min_height": None,
        "safe_search": True,
        "max_results": 3,
        "provider": BrollProvider.LOCAL,
    }
    defaults.update(values)
    return MediaSearchInput.model_validate(defaults)


async def test_output_and_order_are_deterministic_and_schema_valid() -> None:
    provider = LocalMediaProvider()
    provider_input = search_input()

    first = await provider.search(provider_input)
    second = await provider.search(provider_input)

    assert first == second
    assert len(first) == 3
    assert all(MediaCandidateResult.model_validate(candidate) for candidate in first)
    assert [candidate.relevance_score for candidate in first] == sorted(
        (candidate.relevance_score for candidate in first), reverse=True
    )


@pytest.mark.parametrize(
    ("orientation", "dimensions"),
    [
        (BrollOrientation.PORTRAIT, (1080, 1920)),
        (BrollOrientation.LANDSCAPE, (1920, 1080)),
        (BrollOrientation.SQUARE, (1080, 1080)),
        (BrollOrientation.UNKNOWN, (1280, 720)),
    ],
)
async def test_orientation_dimensions(
    orientation: BrollOrientation, dimensions: tuple[int, int]
) -> None:
    candidate = (await LocalMediaProvider().search(search_input(orientation=orientation)))[0]

    assert (candidate.width, candidate.height) == dimensions
    assert candidate.orientation == orientation


async def test_minimum_dimensions_scale_while_preserving_ratio() -> None:
    candidate = (
        await LocalMediaProvider().search(
            search_input(
                orientation=BrollOrientation.PORTRAIT,
                min_width=2160,
                min_height=3000,
            )
        )
    )[0]

    assert (candidate.width, candidate.height) == (2160, 3840)


async def test_video_durations_respect_bounds() -> None:
    candidates = await LocalMediaProvider().search(
        search_input(min_duration_seconds=4.5, max_duration_seconds=5.25, max_results=5)
    )

    assert all(
        candidate.duration_seconds is not None and 4.5 <= candidate.duration_seconds <= 5.25
        for candidate in candidates
    )


async def test_image_candidates_have_no_duration_and_matching_type() -> None:
    candidates = await LocalMediaProvider().search(
        search_input(
            media_type=BrollMediaType.IMAGE,
            min_duration_seconds=0,
            max_duration_seconds=0,
        )
    )

    assert all(candidate.media_type == BrollMediaType.IMAGE for candidate in candidates)
    assert all(candidate.duration_seconds is None for candidate in candidates)
    assert all(candidate.mime_type == "image/jpeg" for candidate in candidates)


async def test_max_results_is_enforced() -> None:
    candidates = await LocalMediaProvider().search(search_input(max_results=1))

    assert len(candidates) == 1


async def test_ids_and_placeholder_urls_are_stable_and_distinct() -> None:
    candidates = await LocalMediaProvider().search(search_input())

    assert len({candidate.external_id for candidate in candidates}) == len(candidates)
    for candidate in candidates:
        urls = {str(candidate.source_url), str(candidate.preview_url), str(candidate.download_url)}
        assert len(urls) == 3
        assert all(url.startswith("https://local.invalid/") for url in urls)


async def test_unicode_query_is_normalized_deterministically() -> None:
    provider = LocalMediaProvider()
    provider_input = search_input(query="  Café   şehir   दृश्य  ")

    candidates = await provider.search(provider_input)

    assert candidates == await provider.search(provider_input)
    assert candidates[0].title.startswith("Café şehir दृश्य")


@pytest.mark.parametrize("query", ["   ", "... !!! —"])
async def test_rejects_empty_or_punctuation_only_query(query: str) -> None:
    unchecked = search_input().model_copy(update={"query": query})

    with pytest.raises(UnusableMediaSearchInputError):
        await LocalMediaProvider().search(unchecked)


async def test_rejects_unsupported_provider() -> None:
    with pytest.raises(UnsupportedMediaProviderError):
        await LocalMediaProvider().search(search_input(provider=BrollProvider.PEXELS))


async def test_rejects_unsupported_language() -> None:
    with pytest.raises(UnsupportedMediaLanguageError):
        await LocalMediaProvider().search(search_input(language="tr-TR"))


@pytest.mark.parametrize(
    "changes",
    [
        {"min_duration_seconds": 5.0, "max_duration_seconds": 2.0},
        {"min_duration_seconds": 0.0, "max_duration_seconds": 0.0},
        {"max_results": 0},
    ],
)
async def test_defensively_rejects_invalid_unchecked_constraints(changes: dict) -> None:
    unchecked = search_input().model_copy(update=changes)

    with pytest.raises(UnusableMediaSearchInputError):
        await LocalMediaProvider().search(unchecked)


async def test_metadata_is_json_safe_and_marks_synthetic_safe_content() -> None:
    metadata = (await LocalMediaProvider().search(search_input()))[0].metadata_data

    assert json.loads(json.dumps(metadata)) == metadata
    assert metadata["synthetic"] is True
    assert metadata["safe_placeholder"] is True
    assert metadata["candidate_index"] == 0
    assert len(metadata["query_hash"]) == 64


async def test_input_is_not_mutated() -> None:
    provider_input = search_input(query="  preserve   spacing  ")
    original = provider_input.model_dump(mode="json")

    await LocalMediaProvider().search(provider_input)

    assert provider_input.model_dump(mode="json") == original
