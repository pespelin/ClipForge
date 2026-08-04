from typing import Any

import pytest

from app.core.exceptions import (
    CompletedAnalysisNotFoundError,
    InvalidClipCandidateIndexError,
    InvalidHookCandidateIndexError,
    ScriptGenerationError,
    ScriptNotFoundError,
    VideoNotFoundError,
)
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video import Transcript, Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.schemas.script import (
    ScriptGenerationOptions,
    ScriptGenerationResult,
    ScriptGeneratorInput,
    ScriptSection,
)
from app.services.script_generation_service import ScriptGenerationService


class FakeVideoRepository:
    def __init__(self, video: Video | None) -> None:
        self.video = video

    async def get(self, video_id: str) -> Video | None:
        if self.video is not None and self.video.id == video_id:
            return self.video
        return None


class FakeAnalysisRepository:
    def __init__(self, analysis: VideoAnalysis | None) -> None:
        self.analysis = analysis

    async def get(self, analysis_id: int) -> VideoAnalysis | None:
        if self.analysis is not None and self.analysis.id == analysis_id:
            return self.analysis
        return None

    async def get_by_video_id(self, video_id: str) -> VideoAnalysis | None:
        if self.analysis is not None and self.analysis.video_id == video_id:
            return self.analysis
        return None


class FakeScriptRepository:
    def __init__(self) -> None:
        self.rows: list[Script] = []
        self.saved_statuses: list[ScriptStatus] = []
        self.commits = 0

    async def create(self, script: Script) -> Script:
        script.id = len(self.rows) + 1
        self.rows.append(script)
        return script

    async def get(self, script_id: int) -> Script | None:
        return next((script for script in self.rows if script.id == script_id), None)

    async def get_by_video_id(self, video_id: str) -> list[Script]:
        return [script for script in self.rows if script.video_id == video_id]

    async def save(self, script: Script) -> Script:
        self.saved_statuses.append(script.status)
        return script

    async def commit(self) -> None:
        self.commits += 1


class FakeGenerator:
    def __init__(self, result: ScriptGenerationResult | dict[str, Any]) -> None:
        self.result = result
        self.calls = 0
        self.generation_input: ScriptGeneratorInput | None = None

    async def generate(self, generation_input: ScriptGeneratorInput):
        self.calls += 1
        self.generation_input = generation_input
        return self.result


class FailingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, generation_input: ScriptGeneratorInput):
        self.calls += 1
        raise RuntimeError("generator unavailable")


def completed_video() -> Video:
    video = Video(
        id="video-1",
        filename="video.mp4",
        status="completed",
        metadata_data={
            "duration": 60.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "file_size": 1024,
        },
    )
    video.transcript = Transcript(language="en", full_text="A complete transcript for scripting.")
    return video


def completed_analysis(status: AnalysisStatus = AnalysisStatus.COMPLETED) -> VideoAnalysis:
    return VideoAnalysis(
        id=7,
        video_id="video-1",
        status=status,
        summary="A concise analysis summary.",
        topics=[{"name": "Editing", "description": None, "relevance": 1.0}],
        keywords=["editing", "story"],
        sentiment="positive",
        hook_candidates=[
            {
                "text": "First hook",
                "start_time": 0.0,
                "end_time": 3.0,
                "reason": None,
                "score": 0.8,
            },
            {
                "text": "Second hook",
                "start_time": 3.0,
                "end_time": 6.0,
                "reason": None,
                "score": 0.7,
            },
        ],
        clip_candidates=[
            {
                "title": "First clip",
                "start_time": 0.0,
                "end_time": 20.0,
                "reason": None,
                "score": 0.9,
            }
        ],
    )


def generation_options(**values) -> ScriptGenerationOptions:
    defaults = {
        "target_duration_seconds": 45,
        "tone": ScriptTone.EDUCATIONAL,
        "language": "en",
        "include_call_to_action": True,
    }
    defaults.update(values)
    return ScriptGenerationOptions.model_validate(defaults)


def generation_result() -> ScriptGenerationResult:
    return ScriptGenerationResult(
        title="Three editing lessons",
        hook="Most edits lose viewers immediately.",
        body="Lead with the strongest visual and remove every unnecessary pause.",
        call_to_action="Follow for more editing tips.",
        full_script=(
            "Most edits lose viewers immediately. Lead with the strongest visual and remove pauses."
        ),
        estimated_duration_seconds=35,
        sections=[
            ScriptSection(
                order=0,
                type="hook",
                text="Most edits lose viewers immediately.",
                estimated_duration_seconds=3,
                source_start_time=0,
                source_end_time=3,
            )
        ],
    )


def make_service(
    *,
    video: Video | None = None,
    analysis: VideoAnalysis | None = None,
    scripts: FakeScriptRepository | None = None,
    generator=None,
) -> tuple[ScriptGenerationService, FakeScriptRepository]:
    script_repository = scripts or FakeScriptRepository()
    service = ScriptGenerationService(
        FakeVideoRepository(video),
        FakeAnalysisRepository(analysis),
        script_repository,
        generator or FakeGenerator(generation_result()),
    )
    return service, script_repository


async def test_create_script_persists_pending_variant_and_options_snapshot() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())
    options = generation_options(preferred_hook_candidate_index=1)

    script = await service.create_script("video-1", options)

    assert script.status == ScriptStatus.PENDING
    assert script.video_id == "video-1"
    assert script.video_analysis_id == 7
    assert script.target_duration_seconds == 45
    assert script.tone is ScriptTone.EDUCATIONAL
    assert script.generation_options == options.model_dump(mode="json")
    assert repository.rows == [script]


async def test_create_script_allows_multiple_variants() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())

    first = await service.create_script("video-1", generation_options())
    second = await service.create_script(
        "video-1", generation_options(target_duration_seconds=30, tone=ScriptTone.DRAMATIC)
    )

    assert first.id != second.id
    assert first.video_analysis_id == second.video_analysis_id
    assert repository.rows == [first, second]


async def test_request_script_generation_commits_pending_variant() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())

    script = await service.request_script_generation("video-1", generation_options())

    assert script.status == ScriptStatus.PENDING
    assert repository.commits == 1


@pytest.mark.parametrize("state", [ScriptStatus.PENDING, ScriptStatus.FAILED])
async def test_prepare_retry_reuses_row_and_commits_pending_state(state) -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())
    existing = Script(
        id=1,
        video_id="video-1",
        video_analysis_id=7,
        status=state,
        target_duration_seconds=45,
        tone=ScriptTone.ENGAGING,
        language="en",
        generation_options=generation_options().model_dump(mode="json"),
        error_message="Old failure",
    )
    repository.rows.append(existing)

    result, should_enqueue = await service.prepare_script_retry(1)

    assert result is existing
    assert should_enqueue is True
    assert existing.status == ScriptStatus.PENDING
    assert existing.error_message is None
    assert repository.commits == 1


@pytest.mark.parametrize("state", [ScriptStatus.GENERATING, ScriptStatus.COMPLETED])
async def test_prepare_retry_does_not_mutate_or_commit_active_script(state) -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())
    existing = Script(
        id=1,
        video_id="video-1",
        video_analysis_id=7,
        status=state,
        target_duration_seconds=45,
        tone=ScriptTone.ENGAGING,
        language="en",
        generation_options=generation_options().model_dump(mode="json"),
    )
    repository.rows.append(existing)

    result, should_enqueue = await service.prepare_script_retry(1)

    assert result is existing
    assert should_enqueue is False
    assert repository.saved_statuses == []
    assert repository.commits == 0


async def test_mark_enqueue_failed_persists_and_commits_useful_error() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())
    existing = await service.create_script("video-1", generation_options())

    await service.mark_script_enqueue_failed(existing, RuntimeError("broker unavailable"))

    assert existing.status == ScriptStatus.FAILED
    assert existing.completed_at is None
    assert existing.error_message == "Script task enqueue failed: broker unavailable"
    assert repository.saved_statuses == [ScriptStatus.FAILED]
    assert repository.commits == 1


async def test_create_script_rejects_missing_video() -> None:
    service, repository = make_service(analysis=completed_analysis())

    with pytest.raises(VideoNotFoundError):
        await service.create_script("missing", generation_options())

    assert repository.rows == []


@pytest.mark.parametrize(
    "analysis",
    [None, completed_analysis(AnalysisStatus.PENDING), completed_analysis(AnalysisStatus.FAILED)],
)
async def test_create_script_requires_completed_analysis(
    analysis: VideoAnalysis | None,
) -> None:
    service, repository = make_service(video=completed_video(), analysis=analysis)

    with pytest.raises(CompletedAnalysisNotFoundError):
        await service.create_script("video-1", generation_options())

    assert repository.rows == []


async def test_create_script_accepts_valid_candidate_indexes() -> None:
    service, _ = make_service(video=completed_video(), analysis=completed_analysis())

    script = await service.create_script(
        "video-1",
        generation_options(
            preferred_hook_candidate_index=1,
            preferred_clip_candidate_index=0,
        ),
    )

    assert script.generation_options["preferred_hook_candidate_index"] == 1
    assert script.generation_options["preferred_clip_candidate_index"] == 0


async def test_create_script_rejects_out_of_range_hook_index() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())

    with pytest.raises(InvalidHookCandidateIndexError):
        await service.create_script("video-1", generation_options(preferred_hook_candidate_index=2))

    assert repository.rows == []


async def test_create_script_rejects_out_of_range_clip_index() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())

    with pytest.raises(InvalidClipCandidateIndexError):
        await service.create_script("video-1", generation_options(preferred_clip_candidate_index=1))

    assert repository.rows == []


async def test_process_script_maps_result_and_selected_candidates() -> None:
    generator = FakeGenerator(generation_result().model_dump())
    service, repository = make_service(
        video=completed_video(), analysis=completed_analysis(), generator=generator
    )
    script = await service.create_script(
        "video-1",
        generation_options(
            preferred_hook_candidate_index=1,
            preferred_clip_candidate_index=0,
        ),
    )

    result = await service.process_script(script.id)

    assert repository.saved_statuses == [ScriptStatus.GENERATING, ScriptStatus.COMPLETED]
    assert result.status == ScriptStatus.COMPLETED
    assert result.title == "Three editing lessons"
    assert result.hook == "Most edits lose viewers immediately."
    assert result.estimated_duration_seconds == 35
    assert result.sections[0]["type"] == "hook"
    assert result.completed_at is not None
    assert result.error_message is None
    assert generator.generation_input.transcript == "A complete transcript for scripting."
    assert generator.generation_input.video_metadata.duration == 60
    assert generator.generation_input.selected_hook_candidate.text == "Second hook"
    assert generator.generation_input.selected_clip_candidate.title == "First clip"


async def test_completed_script_is_idempotent() -> None:
    generator = FakeGenerator(generation_result())
    scripts = FakeScriptRepository()
    existing = Script(
        id=1,
        video_id="video-1",
        video_analysis_id=7,
        status=ScriptStatus.COMPLETED,
        target_duration_seconds=45,
        tone=ScriptTone.ENGAGING,
        language="en",
        generation_options=generation_options().model_dump(mode="json"),
    )
    scripts.rows.append(existing)
    service, _ = make_service(
        video=completed_video(),
        analysis=completed_analysis(),
        scripts=scripts,
        generator=generator,
    )

    result = await service.process_script(1)

    assert result is existing
    assert generator.calls == 0
    assert scripts.saved_statuses == []


async def test_generator_failure_persists_failed_state_and_app_error() -> None:
    generator = FailingGenerator()
    service, repository = make_service(
        video=completed_video(), analysis=completed_analysis(), generator=generator
    )
    script = await service.create_script("video-1", generation_options())

    with pytest.raises(ScriptGenerationError) as error_info:
        await service.process_script(script.id)

    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert script.status == ScriptStatus.FAILED
    assert script.completed_at is None
    assert script.error_message == "generator unavailable"
    assert repository.saved_statuses == [ScriptStatus.GENERATING, ScriptStatus.FAILED]


async def test_get_script_returns_existing_and_raises_for_missing() -> None:
    service, repository = make_service(video=completed_video(), analysis=completed_analysis())
    script = await service.create_script("video-1", generation_options())

    assert await service.get_script(script.id) is script
    with pytest.raises(ScriptNotFoundError):
        await service.get_script(999)

    assert repository.rows == [script]


async def test_list_scripts_for_video_returns_variants() -> None:
    service, _ = make_service(video=completed_video(), analysis=completed_analysis())
    first = await service.create_script("video-1", generation_options())
    second = await service.create_script("video-1", generation_options())

    assert await service.list_scripts_for_video("video-1") == [first, second]
