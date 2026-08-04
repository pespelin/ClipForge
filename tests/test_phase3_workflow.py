import asyncio
from datetime import UTC, datetime

import pytest
from celery.exceptions import Retry
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_video_analysis_service, get_video_service
from app.api.v1.endpoints import video_analysis as analysis_endpoint
from app.api.v1.endpoints import videos as videos_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import AnalysisProcessingError
from app.models.video import Transcript, Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.providers.analysis import LocalVideoAnalyzer
from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import VideoAnalysisResponse, VideoAnalysisStatusResponse
from app.services.storage_service import StorageService
from app.services.video_analysis_service import VideoAnalysisService
from app.services.video_service import VideoService
from app.tasks import video as video_task
from app.tasks import video_analysis as analysis_task


class InMemorySession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class InMemoryVideoRepository:
    def __init__(self) -> None:
        self.videos: dict[str, Video] = {}

    async def create(self, video: Video) -> Video:
        self.videos[video.id] = video
        return video

    async def get(self, video_id: str) -> Video | None:
        return self.videos.get(video_id)

    async def update_status(self, video: Video, status: str) -> None:
        video.status = status

    async def upsert_transcript(
        self, video: Video, language: str | None, full_text: str
    ) -> Transcript:
        if video.transcript is None:
            video.transcript = Transcript(language=language, full_text=full_text)
        else:
            video.transcript.language = language
            video.transcript.full_text = full_text
        return video.transcript


class InMemoryAnalysisRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.rows: dict[str, VideoAnalysis] = {}
        self.create_calls = 0

    async def create(self, analysis: VideoAnalysis) -> VideoAnalysis:
        self.create_calls += 1
        timestamp = datetime.now(UTC)
        analysis.id = self.create_calls
        analysis.created_at = timestamp
        analysis.updated_at = timestamp
        analysis.topics = analysis.topics or []
        analysis.keywords = analysis.keywords or []
        analysis.hook_candidates = analysis.hook_candidates or []
        analysis.clip_candidates = analysis.clip_candidates or []
        self.rows[analysis.video_id] = analysis
        return analysis

    async def get_by_video_id(self, video_id: str) -> VideoAnalysis | None:
        return self.rows.get(video_id)

    async def save(self, analysis: VideoAnalysis) -> VideoAnalysis:
        analysis.updated_at = datetime.now(UTC)
        self.rows[analysis.video_id] = analysis
        return analysis

    async def commit(self) -> None:
        await self.session.commit()


class FakeFFmpeg:
    def extract_metadata(self, video_path) -> VideoMetadata:
        return VideoMetadata(
            duration=45.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            audio_codec="aac",
            file_size=video_path.stat().st_size,
        )

    def extract_audio(self, video_path, audio_path):
        audio_path.write_bytes(b"audio")
        return audio_path


class FakeWhisper:
    def transcribe(self, audio_path):
        return {
            "transcript": (
                "Great editing creates a strong story. "
                "Editing helps viewers understand the main topic."
            ),
            "language": "en",
            "segments": [],
            "timestamps": [],
        }


class FailingWhisper:
    def transcribe(self, audio_path):
        raise RuntimeError("transcription failed")


class CountingLocalAnalyzer(LocalVideoAnalyzer):
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, transcript: str, *, metadata: VideoMetadata | None):
        self.calls += 1
        return await super().analyze(transcript, metadata=metadata)


class FailingAnalyzer:
    async def analyze(self, transcript: str, *, metadata: VideoMetadata | None):
        raise RuntimeError("analysis adapter failed")


class WorkflowHarness:
    def __init__(self, tmp_path, monkeypatch, *, whisper=None) -> None:
        self.session = InMemorySession()
        self.video_repository = InMemoryVideoRepository()
        self.analysis_repository = InMemoryAnalysisRepository(self.session)
        self.analyzer = CountingLocalAnalyzer()
        self.processing_queue: list[str] = []
        self.analysis_queue: list[str] = []
        self.video_service = VideoService(
            session=self.session,
            storage=StorageService(tmp_path / "videos", 1024 * 1024),
            ffmpeg=FakeFFmpeg(),
            whisper=whisper or FakeWhisper(),
        )
        self.video_service.repository = self.video_repository
        self.analysis_service = VideoAnalysisService(
            self.video_repository, self.analysis_repository, self.analyzer
        )

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_video_service] = lambda: self.video_service
        self.app.dependency_overrides[get_video_analysis_service] = lambda: self.analysis_service

        monkeypatch.setattr(videos_endpoint.process_video, "delay", self.processing_queue.append)
        monkeypatch.setattr(video_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(video_task, "get_video_service", lambda session: self.video_service)
        monkeypatch.setattr(video_task.analyze_video, "delay", self.analysis_queue.append)
        monkeypatch.setattr(analysis_endpoint.analyze_video, "delay", self.analysis_queue.append)
        monkeypatch.setattr(analysis_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(analysis_task, "VideoRepository", lambda session: self.video_repository)
        monkeypatch.setattr(
            analysis_task,
            "VideoAnalysisRepository",
            lambda session: self.analysis_repository,
        )
        monkeypatch.setattr(analysis_task, "LocalVideoAnalyzer", lambda: self.analyzer)

    def client(self) -> TestClient:
        return TestClient(self.app)

    def upload(self, client: TestClient) -> str:
        response = client.post(
            "/api/v1/videos/upload",
            files={"file": ("demo.mp4", b"video bytes", "video/mp4")},
        )
        assert response.status_code == 202
        return response.json()["video_id"]

    def process(self, video_id: str) -> None:
        video_task.process_video.run(video_id)

    def analyze(self, video_id: str) -> dict[str, str]:
        return asyncio.run(analysis_task._run_analysis(video_id))


def test_complete_phase2_to_phase3_workflow_is_idempotent(tmp_path, monkeypatch) -> None:
    harness = WorkflowHarness(tmp_path, monkeypatch)

    with harness.client() as client:
        video_id = harness.upload(client)
        assert harness.processing_queue == [video_id]

        harness.process(video_id)
        video = harness.video_repository.videos[video_id]
        assert video.status == "completed"
        assert video.transcript is not None
        assert harness.analysis_queue == [video_id]

        task_result = harness.analyze(harness.analysis_queue.pop())
        assert task_result == {"video_id": video_id, "analysis_status": "completed"}

        response = client.get(f"/api/v1/videos/{video_id}/analysis")
        completed = VideoAnalysisResponse.model_validate(response.json())
        assert response.status_code == 200
        assert completed.status == AnalysisStatus.COMPLETED
        assert completed.summary
        assert completed.topics
        assert completed.hook_candidates
        assert completed.clip_candidates

        repeated_request = client.post(f"/api/v1/videos/{video_id}/analysis")
        assert repeated_request.status_code == 200
        assert harness.analysis_queue == []

        repeated_task = harness.analyze(video_id)
        assert repeated_task["analysis_status"] == "completed"
        assert harness.analyzer.calls == 1
        assert harness.analysis_repository.create_calls == 1
        assert len(harness.analysis_repository.rows) == 1


def test_transcription_failure_prevents_analysis_enqueue(tmp_path, monkeypatch) -> None:
    harness = WorkflowHarness(tmp_path, monkeypatch, whisper=FailingWhisper())

    with harness.client() as client:
        video_id = harness.upload(client)

    with pytest.raises((RuntimeError, Retry)):
        harness.process(video_id)

    assert harness.video_repository.videos[video_id].status == "failed"
    assert harness.analysis_queue == []
    assert harness.analysis_repository.rows == {}


def test_analysis_failure_persists_failed_status_for_api(tmp_path, monkeypatch) -> None:
    harness = WorkflowHarness(tmp_path, monkeypatch)

    with harness.client() as client:
        video_id = harness.upload(client)
        harness.process(video_id)
        monkeypatch.setattr(analysis_task, "LocalVideoAnalyzer", FailingAnalyzer)

        with pytest.raises(AnalysisProcessingError):
            harness.analyze(video_id)

        response = client.get(f"/api/v1/videos/{video_id}/analysis")
        failed = VideoAnalysisStatusResponse.model_validate(response.json())

    assert response.status_code == 200
    assert failed.status == AnalysisStatus.FAILED
    assert failed.error_message == "analysis adapter failed"
