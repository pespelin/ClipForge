import pytest

from app.providers.analysis import LocalVideoAnalyzer, UnusableTranscriptError, VideoAnalyzer
from app.schemas.video import VideoMetadata


def metadata(duration: float | None = 90.0) -> VideoMetadata:
    return VideoMetadata(duration=duration, file_size=1024)


async def test_output_is_deterministic_and_protocol_compatible() -> None:
    analyzer: VideoAnalyzer = LocalVideoAnalyzer()
    transcript = "Great editing makes a good story. Editing creates better videos."

    first = await analyzer.analyze(transcript, metadata=metadata())
    second = await analyzer.analyze(transcript, metadata=metadata())

    assert first == second


async def test_summary_normalizes_whitespace_and_does_not_cut_words() -> None:
    analyzer = LocalVideoAnalyzer()
    transcript = "  First\n\tidea   " + "completeword " * 30

    result = await analyzer.analyze(transcript, metadata=None)

    assert "\n" not in result.summary
    assert "  " not in result.summary
    assert len(result.summary) <= 240
    assert result.summary.split()[-1] == "completeword"


async def test_keywords_are_unique_and_ordered_by_frequency_then_alphabetically() -> None:
    result = await LocalVideoAnalyzer().analyze(
        "Beta alpha beta gamma alpha delta beta gamma", metadata=None
    )

    assert result.keywords == ["beta", "alpha", "gamma", "delta"]
    assert len(result.keywords) == len(set(result.keywords))


async def test_keywords_filter_stop_words_punctuation_and_short_tokens() -> None:
    result = await LocalVideoAnalyzer().analyze(
        "The, and! an AI video; video with a story.", metadata=None
    )

    assert result.keywords == ["video", "story"]


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("A great excellent success and a problem", "positive"),
        ("A terrible bad failure with one good point", "negative"),
        ("A camera records an ordinary table", "neutral"),
        ("A good result and a bad result", "neutral"),
    ],
)
async def test_rule_based_sentiment(transcript: str, expected: str) -> None:
    result = await LocalVideoAnalyzer().analyze(transcript, metadata=None)

    assert result.sentiment == expected


async def test_missing_metadata_uses_deterministic_estimated_windows() -> None:
    result = await LocalVideoAnalyzer().analyze("A concise useful transcript", metadata=None)

    assert result.hook_candidates[0].start_time == 0.0
    assert result.hook_candidates[0].end_time > 0
    assert result.clip_candidates[0].end_time > result.clip_candidates[0].start_time


@pytest.mark.parametrize("duration", [0.1, 1.0, 12.5])
async def test_short_positive_duration_bounds_all_candidates(duration: float) -> None:
    result = await LocalVideoAnalyzer().analyze(
        "Short but useful transcript content", metadata=metadata(duration)
    )

    candidates = [*result.hook_candidates, *result.clip_candidates]
    assert candidates
    assert all(candidate.start_time >= 0 for candidate in candidates)
    assert all(candidate.end_time > candidate.start_time for candidate in candidates)
    assert all(candidate.end_time <= duration for candidate in candidates)
    assert all(
        candidate.score is not None and 0 <= candidate.score <= 1 for candidate in candidates
    )


@pytest.mark.parametrize("duration", [0.0, -1.0])
async def test_non_positive_duration_is_treated_as_unusable_metadata(duration: float) -> None:
    supplied_metadata = metadata(duration)

    result = await LocalVideoAnalyzer().analyze(
        "Transcript remains analyzable", metadata=supplied_metadata
    )

    assert supplied_metadata.duration == duration
    assert result.hook_candidates[0].end_time > 0
    assert result.clip_candidates[0].end_time > 0


async def test_stop_word_only_transcript_returns_general_topic() -> None:
    result = await LocalVideoAnalyzer().analyze("the and with this", metadata=None)

    assert result.keywords == []
    assert result.topics[0].name == "General"
    assert result.topics[0].relevance == 0


async def test_very_short_transcript_still_produces_valid_candidates() -> None:
    result = await LocalVideoAnalyzer().analyze("Hi", metadata=None)

    assert result.summary == "Hi"
    assert result.keywords == []
    assert result.hook_candidates[0].end_time > 0
    assert result.clip_candidates[0].end_time > 0


@pytest.mark.parametrize("transcript", ["", " \n\t ", "... !!! ---", "x" * 241])
async def test_unusable_transcript_raises_explicit_error(transcript: str) -> None:
    with pytest.raises(UnusableTranscriptError):
        await LocalVideoAnalyzer().analyze(transcript, metadata=None)
