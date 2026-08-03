from typing import Any

import pytest

from app.core.exceptions import (
    AnalysisNotFoundError,
    AnalysisProcessingError,
    TranscriptNotReadyError,
    VideoNotFoundError,
)
from app.models.video import Transcript, Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.schemas.video_analysis import (
    ClipCandidate,
    HookCandidate,
    TopicResult,
    VideoAnalysisResult,
)
from app.services.video_analysis_service import VideoAnalysisService


class FakeVideoRepository:
    def __init__(self, video: Video | None) -> None:
        self.video = video

    async def get(self, video_id: str) -> Video | None:
        if self.video is not None and self.video.id == video_id:
            return self.video
        return None


class FakeAnalysisRepository:
    def __init__(self, analysis: VideoAnalysis | None = None) -> None:
        self.analysis = analysis
        self.create_calls = 0
        self.saved_statuses: list[AnalysisStatus] = []

    async def create(self, analysis: VideoAnalysis) -> VideoAnalysis:
        self.create_calls += 1
        self.analysis = analysis
        return analysis

    async def get_by_video_id(self, video_id: str) -> VideoAnalysis | None:
        if self.analysis is not None and self.analysis.video_id == video_id:
            return self.analysis
        return None

    async def save(self, analysis: VideoAnalysis) -> VideoAnalysis:
        self.analysis = analysis
        self.saved_statuses.append(analysis.status)
        return analysis


class FakeAnalyzer:
    def __init__(self, result: VideoAnalysisResult | dict[str, Any]) -> None:
        self.result = result
        self.calls = 0
        self.transcript: str | None = None
        self.metadata = None

    async def analyze(self, transcript: str, *, metadata):
        self.calls += 1
        self.transcript = transcript
        self.metadata = metadata
        return self.result


class FailingAnalyzer:
    async def analyze(self, transcript: str, *, metadata):
        raise RuntimeError("analyzer unavailable")


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
    video.transcript = Transcript(language="en", full_text="A complete transcript.")
    return video


def analysis_result() -> VideoAnalysisResult:
    return VideoAnalysisResult(
        summary="Summary",
        topics=[TopicResult(name="Topic", relevance=0.9)],
        keywords=["keyword"],
        sentiment="positive",
        hook_candidates=[HookCandidate(text="Hook", start_time=0.0, end_time=3.0, score=0.8)],
        clip_candidates=[ClipCandidate(title="Clip", start_time=5.0, end_time=25.0, score=0.95)],
    )


def make_service(
    video: Video | None,
    analysis_repository: FakeAnalysisRepository,
    analyzer,
) -> VideoAnalysisService:
    return VideoAnalysisService(FakeVideoRepository(video), analysis_repository, analyzer)


async def test_create_or_get_analysis_creates_pending_analysis() -> None:
    repository = FakeAnalysisRepository()
    analyzer = FakeAnalyzer(analysis_result())
    service = make_service(completed_video(), repository, analyzer)

    first = await service.create_or_get_analysis("video-1")
    second = await service.create_or_get_analysis("video-1")

    assert first is second
    assert first.status == AnalysisStatus.PENDING
    assert repository.create_calls == 1


async def test_process_analysis_persists_structured_completed_result() -> None:
    repository = FakeAnalysisRepository()
    analyzer = FakeAnalyzer(analysis_result().model_dump())
    service = make_service(completed_video(), repository, analyzer)

    analysis = await service.process_analysis("video-1")

    assert repository.saved_statuses == [AnalysisStatus.PROCESSING, AnalysisStatus.COMPLETED]
    assert analysis.status == AnalysisStatus.COMPLETED
    assert analysis.summary == "Summary"
    assert analysis.topics == [{"name": "Topic", "description": None, "relevance": 0.9}]
    assert analysis.keywords == ["keyword"]
    assert analysis.hook_candidates[0]["text"] == "Hook"
    assert analysis.clip_candidates[0]["title"] == "Clip"
    assert analysis.completed_at is not None
    assert analysis.error_message is None
    assert analyzer.transcript == "A complete transcript."
    assert analyzer.metadata.duration == 60.0


async def test_completed_analysis_is_idempotent() -> None:
    existing = VideoAnalysis(video_id="video-1", status=AnalysisStatus.COMPLETED)
    repository = FakeAnalysisRepository(existing)
    analyzer = FakeAnalyzer(analysis_result())
    service = make_service(completed_video(), repository, analyzer)

    result = await service.process_analysis("video-1")

    assert result is existing
    assert analyzer.calls == 0
    assert repository.create_calls == 0
    assert repository.saved_statuses == []


@pytest.mark.parametrize(
    "video",
    [
        Video(id="video-1", filename="video.mp4", status="processing"),
        Video(id="video-1", filename="video.mp4", status="completed"),
    ],
)
async def test_process_analysis_requires_ready_transcript(video: Video) -> None:
    repository = FakeAnalysisRepository()
    service = make_service(video, repository, FakeAnalyzer(analysis_result()))

    with pytest.raises(TranscriptNotReadyError):
        await service.process_analysis("video-1")

    assert repository.create_calls == 0


async def test_process_analysis_rejects_missing_video() -> None:
    repository = FakeAnalysisRepository()
    service = make_service(None, repository, FakeAnalyzer(analysis_result()))

    with pytest.raises(VideoNotFoundError):
        await service.process_analysis("missing")


async def test_analyzer_failure_persists_failed_state_and_reraises_app_error() -> None:
    repository = FakeAnalysisRepository()
    service = make_service(completed_video(), repository, FailingAnalyzer())

    with pytest.raises(AnalysisProcessingError) as error_info:
        await service.process_analysis("video-1")

    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert repository.analysis.status == AnalysisStatus.FAILED
    assert repository.analysis.error_message == "analyzer unavailable"
    assert repository.analysis.completed_at is None
    assert repository.saved_statuses == [AnalysisStatus.PROCESSING, AnalysisStatus.FAILED]


async def test_get_analysis_returns_existing_analysis() -> None:
    existing = VideoAnalysis(video_id="video-1", status=AnalysisStatus.PENDING)
    service = make_service(
        completed_video(), FakeAnalysisRepository(existing), FakeAnalyzer(analysis_result())
    )

    assert await service.get_analysis("video-1") is existing


async def test_get_analysis_raises_when_analysis_is_missing() -> None:
    service = make_service(
        completed_video(), FakeAnalysisRepository(), FakeAnalyzer(analysis_result())
    )

    with pytest.raises(AnalysisNotFoundError):
        await service.get_analysis("video-1")
