import pytest

from app.models.script import ScriptTone
from app.providers.script import (
    LocalScriptGenerator,
    ScriptGenerator,
    UnsupportedScriptLanguageError,
    UnusableScriptInputError,
)
from app.schemas.script import (
    ScriptGenerationOptions,
    ScriptGenerationResult,
    ScriptGeneratorInput,
)
from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import (
    ClipCandidate,
    HookCandidate,
    TopicResult,
    VideoAnalysisResult,
)


def generator_input(
    *,
    tone: ScriptTone = ScriptTone.ENGAGING,
    language: str = "en",
    include_call_to_action: bool = True,
    target_duration_seconds: float = 30,
    transcript: str = "A practical transcript about editing a clear and engaging short video.",
    summary: str = "Strong editing keeps the story clear and holds viewer attention.",
    topics: list[TopicResult] | None = None,
    keywords: list[str] | None = None,
    hooks: list[HookCandidate] | None = None,
    clips: list[ClipCandidate] | None = None,
    selected_hook: HookCandidate | None = None,
    selected_clip: ClipCandidate | None = None,
    metadata: VideoMetadata | None = None,
) -> ScriptGeneratorInput:
    resolved_topics = (
        topics if topics is not None else [TopicResult(name="Video Editing", relevance=0.9)]
    )
    resolved_keywords = keywords if keywords is not None else ["editing", "story"]
    resolved_hooks = (
        hooks
        if hooks is not None
        else [
            HookCandidate(
                text="This editing mistake loses viewers fast",
                start_time=0,
                end_time=3,
                score=0.8,
            ),
            HookCandidate(
                text="The strongest hook wins attention",
                start_time=3,
                end_time=6,
                score=0.9,
            ),
        ]
    )
    resolved_clips = (
        clips
        if clips is not None
        else [
            ClipCandidate(
                title="Editing example",
                start_time=8,
                end_time=24,
                score=0.9,
            )
        ]
    )
    return ScriptGeneratorInput(
        video_id="video-1",
        video_analysis_id=1,
        transcript=transcript,
        video_metadata=metadata or VideoMetadata(duration=60, file_size=1024),
        analysis=VideoAnalysisResult(
            summary=summary,
            topics=resolved_topics,
            keywords=resolved_keywords,
            sentiment="positive",
            hook_candidates=resolved_hooks,
            clip_candidates=resolved_clips,
        ),
        options=ScriptGenerationOptions(
            target_duration_seconds=target_duration_seconds,
            tone=tone,
            language=language,
            include_call_to_action=include_call_to_action,
        ),
        selected_hook_candidate=selected_hook,
        selected_clip_candidate=selected_clip,
    )


async def test_output_is_deterministic_protocol_compatible_and_schema_valid() -> None:
    generator: ScriptGenerator = LocalScriptGenerator()
    generation_input = generator_input()

    first = await generator.generate(generation_input)
    second = await generator.generate(generation_input)

    assert first == second
    assert ScriptGenerationResult.model_validate(first) == first
    assert first.full_script == " ".join([first.hook, first.body, first.call_to_action])


async def test_selected_hook_is_preferred() -> None:
    selected = HookCandidate(
        text="Use this deliberately selected opening",
        start_time=10,
        end_time=14,
        score=0.1,
    )

    result = await LocalScriptGenerator().generate(generator_input(selected_hook=selected))

    assert result.hook.startswith("Use this deliberately selected opening")
    assert result.sections[0].source_start_time == 10
    assert result.sections[0].source_end_time == 14


async def test_strongest_hook_is_used_before_summary_fallback() -> None:
    result = await LocalScriptGenerator().generate(generator_input())

    assert result.hook.startswith("The strongest hook wins attention")


async def test_fallback_hook_uses_summary_without_candidates() -> None:
    result = await LocalScriptGenerator().generate(generator_input(hooks=[]))

    assert result.hook.startswith("Strong editing keeps the story")


async def test_selected_clip_supplies_body_source_timestamps() -> None:
    selected = ClipCandidate(
        title="Selected visual example",
        start_time=12,
        end_time=28,
        score=0.2,
    )

    result = await LocalScriptGenerator().generate(generator_input(selected_clip=selected))

    body_section = result.sections[1]
    assert "Selected visual example" in result.body
    assert body_section.source_start_time == 12
    assert body_section.source_end_time == 28


@pytest.mark.parametrize("tone", list(ScriptTone))
async def test_tone_templates_are_deterministic_and_distinct(tone: ScriptTone) -> None:
    result = await LocalScriptGenerator().generate(generator_input(tone=tone, clips=[]))

    assert result.body
    assert tone.value not in result.body.lower()


async def test_tone_variants_produce_different_body_phrasing() -> None:
    generator = LocalScriptGenerator()
    bodies = {
        tone: (await generator.generate(generator_input(tone=tone, clips=[]))).body
        for tone in ScriptTone
    }

    assert len(set(bodies.values())) == len(ScriptTone)


async def test_call_to_action_enabled() -> None:
    result = await LocalScriptGenerator().generate(generator_input())

    assert result.call_to_action is not None
    assert result.sections[-1].type == "call_to_action"
    assert result.full_script.endswith(result.call_to_action)


async def test_call_to_action_disabled() -> None:
    result = await LocalScriptGenerator().generate(generator_input(include_call_to_action=False))

    assert result.call_to_action is None
    assert [section.type for section in result.sections] == ["hook", "body"]
    assert result.full_script == f"{result.hook} {result.body}"


async def test_duration_estimate_uses_word_budget_and_150_wpm() -> None:
    target = 5
    result = await LocalScriptGenerator().generate(generator_input(target_duration_seconds=target))
    expected = round(len(result.full_script.split()) / 2.5, 2)

    assert result.estimated_duration_seconds == expected
    assert result.estimated_duration_seconds <= target


async def test_very_short_target_uses_minimum_schema_valid_structure() -> None:
    result = await LocalScriptGenerator().generate(generator_input(target_duration_seconds=0.1))

    assert result.hook
    assert result.body
    assert result.call_to_action
    assert result.estimated_duration_seconds >= 0


async def test_missing_metadata_omits_fallback_timestamps() -> None:
    generation_input = generator_input(hooks=[], clips=[])
    generation_input.video_metadata = None

    result = await LocalScriptGenerator().generate(generation_input)

    assert result.sections[0].source_start_time is None
    assert result.sections[0].source_end_time is None
    assert result.sections[1].source_start_time is None
    assert result.sections[1].source_end_time is None


async def test_candidate_timestamps_are_clamped_to_short_video_duration() -> None:
    result = await LocalScriptGenerator().generate(
        generator_input(metadata=VideoMetadata(duration=2, file_size=1024))
    )

    sourced_sections = [section for section in result.sections if section.source_end_time]
    assert sourced_sections
    assert all(section.source_end_time <= 2 for section in sourced_sections)
    assert all(section.source_end_time > section.source_start_time for section in sourced_sections)


async def test_empty_topics_keywords_and_candidates_use_summary() -> None:
    result = await LocalScriptGenerator().generate(
        generator_input(topics=[], keywords=[], hooks=[], clips=[])
    )

    assert result.title.startswith("Strong Editing")
    assert "main idea" in result.body
    assert result.hook


async def test_minimal_transcript_and_analysis_remain_valid() -> None:
    result = await LocalScriptGenerator().generate(
        generator_input(
            transcript="Hi",
            summary="Hi",
            topics=[],
            keywords=[],
            hooks=[],
            clips=[],
            metadata=VideoMetadata(duration=0.2, file_size=1),
            target_duration_seconds=0.2,
        )
    )

    assert result.title
    assert result.hook == "Hi."
    assert result.body
    assert all(
        section.source_end_time is None or section.source_end_time <= 0.2
        for section in result.sections
    )


async def test_unsupported_language_raises_explicit_error() -> None:
    with pytest.raises(UnsupportedScriptLanguageError, match="English only"):
        await LocalScriptGenerator().generate(generator_input(language="fr"))


async def test_unusable_input_raises_explicit_error() -> None:
    with pytest.raises(UnusableScriptInputError, match="no meaningful source text"):
        await LocalScriptGenerator().generate(
            generator_input(
                transcript="...",
                summary="!!!",
                topics=[],
                keywords=[],
                hooks=[],
                clips=[],
            )
        )


async def test_input_is_not_mutated() -> None:
    generation_input = generator_input()
    before = generation_input.model_dump(mode="json")

    await LocalScriptGenerator().generate(generation_input)

    assert generation_input.model_dump(mode="json") == before


async def test_sections_have_stable_order_valid_durations_and_ranges() -> None:
    result = await LocalScriptGenerator().generate(generator_input())

    assert [section.order for section in result.sections] == list(range(len(result.sections)))
    assert all(section.estimated_duration_seconds >= 0 for section in result.sections)
    assert all(
        section.source_start_time is None
        or (
            section.source_start_time >= 0
            and section.source_end_time is not None
            and section.source_end_time > section.source_start_time
        )
        for section in result.sections
    )
