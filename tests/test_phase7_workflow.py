import asyncio
import hashlib
import json
import shutil
import struct
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_video_render_service
from app.api.v1.endpoints import video_renders as render_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import VideoRenderingError
from app.models.broll import (
    BrollAsset,
    BrollAssetStatus,
    BrollCollection,
    BrollCollectionStatus,
    BrollMediaType,
    BrollProvider,
)
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video_render import VideoRender, VideoRenderStatus
from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrack, VoiceTrackStatus
from app.providers.render import FFmpegVideoRenderer
from app.schemas.video_render import VideoRenderResponse, VideoRenderStatusResponse
from app.services.video_render_service import VideoRenderService
from app.tasks import video_rendering as render_task

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class WorkflowState:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.script = Script(
            id=4,
            video_id="video-1",
            video_analysis_id=7,
            status=ScriptStatus.COMPLETED,
            title="Purposeful editing",
            hook="Start with your strongest visual.",
            body="Keep every cut intentional.",
            full_script="Start with your strongest visual. Keep every cut intentional.",
            estimated_duration_seconds=0.6,
            target_duration_seconds=30,
            tone=ScriptTone.EDUCATIONAL,
            language="en",
            generation_options={},
            sections=[
                {"order": 0, "type": "hook", "text": "Start with your strongest visual."},
                {"order": 1, "type": "body", "text": "Keep every cut intentional."},
            ],
            completed_at=now,
        )
        self.voice = VoiceTrack(
            id=8,
            script_id=4,
            status=VoiceTrackStatus.COMPLETED,
            provider="local",
            voice="default",
            style=VoiceStyle.NEUTRAL,
            language="en",
            audio_format=AudioFormat.WAV,
            sample_rate_hz=8000,
            speaking_rate=1,
            pitch=0,
            volume_gain_db=0,
            generation_options={},
            storage_key="voice/8/audio.wav",
            duration_seconds=0.6,
            file_size_bytes=0,
            segments=[
                {
                    "order": 0,
                    "section_type": "hook",
                    "text": "Start with your strongest visual.",
                    "audio_start_time": 0,
                    "audio_end_time": 0.3,
                    "source_script_section_order": 0,
                },
                {
                    "order": 1,
                    "section_type": "body",
                    "text": "Keep every cut intentional.",
                    "audio_start_time": 0.3,
                    "audio_end_time": 0.6,
                    "source_script_section_order": 1,
                },
            ],
            completed_at=now,
        )
        self.collection = BrollCollection(
            id=12,
            script_id=4,
            status=BrollCollectionStatus.COMPLETED,
            provider=BrollProvider.LOCAL,
            query_strategy="section_keywords",
            retrieval_options={},
            completed_at=now,
        )
        self.asset = BrollAsset(
            id=21,
            collection_id=12,
            script_section_order=0,
            provider=BrollProvider.LOCAL,
            external_id="local-metadata-only",
            media_type=BrollMediaType.VIDEO,
            status=BrollAssetStatus.SELECTED,
            query="purposeful editing",
            title="Purposeful editing",
            source_url="https://local.invalid/source/local-metadata-only",
            download_url="https://local.invalid/download/local-metadata-only",
            storage_key=None,
            width=1080,
            height=1920,
            duration_seconds=0.3,
            orientation="portrait",
            relevance_score=1,
            metadata_data={"synthetic": True},
        )
        self.renders: dict[int, VideoRender] = {}
        self.next_render_id = 1
        self.events: list[str] = []
        self.saved_statuses: list[tuple[int, VideoRenderStatus]] = []


class InMemorySession:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closes += 1

    async def commit(self) -> None:
        self.commits += 1
        self.state.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.state.events.append("rollback")


class InMemoryScriptRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, script_id: int) -> Script | None:
        return self.state.script if self.state.script.id == script_id else None


class InMemoryVoiceTrackRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, voice_track_id: int) -> VoiceTrack | None:
        return self.state.voice if self.state.voice.id == voice_track_id else None


class InMemoryCollectionRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, collection_id: int) -> BrollCollection | None:
        return self.state.collection if self.state.collection.id == collection_id else None


class InMemoryAssetRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get_by_collection_id(self, collection_id: int) -> list[BrollAsset]:
        return [self.state.asset] if self.state.asset.collection_id == collection_id else []


class InMemoryRenderRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.state = session.state

    async def create(self, video_render: VideoRender) -> VideoRender:
        video_render.id = self.state.next_render_id
        self.state.next_render_id += 1
        now = datetime.now(UTC)
        video_render.created_at = now
        video_render.updated_at = now
        self.state.renders[video_render.id] = video_render
        self.state.events.append(f"create:{video_render.id}")
        return video_render

    async def get(self, render_id: int) -> VideoRender | None:
        return self.state.renders.get(render_id)

    async def get_by_script_id(self, script_id: int) -> list[VideoRender]:
        rows = [row for row in self.state.renders.values() if row.script_id == script_id]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    async def save(self, video_render: VideoRender) -> VideoRender:
        video_render.updated_at = datetime.now(UTC)
        self.state.renders[video_render.id] = video_render
        self.state.saved_statuses.append((video_render.id, video_render.status))
        self.state.events.append(f"save:{video_render.id}:{video_render.status.value}")
        return video_render

    async def commit(self) -> None:
        await self.session.commit()


class CountingFFmpegVideoRenderer(FFmpegVideoRenderer):
    def __init__(self, storage_root: Path) -> None:
        super().__init__(storage_root)
        self.calls = 0
        self.inputs = []

    async def render(self, render_input):
        self.calls += 1
        self.inputs.append(render_input)
        return await super().render(render_input)


class FailingRenderer:
    async def render(self, render_input):
        raise RuntimeError("controlled renderer failure")


class Phase7Harness:
    def __init__(self, monkeypatch, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.state = WorkflowState()
        self.session = InMemorySession(self.state)
        self.renderer = CountingFFmpegVideoRenderer(storage_root)
        self.queued: list[int] = []
        self.publication_commits: list[int] = []
        self.script_repository = InMemoryScriptRepository(self.session)
        self.voice_repository = InMemoryVoiceTrackRepository(self.session)
        self.collection_repository = InMemoryCollectionRepository(self.session)
        self.asset_repository = InMemoryAssetRepository(self.session)
        self.render_repository = InMemoryRenderRepository(self.session)
        self.service = self._service(self.renderer)

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_video_render_service] = lambda: self.service

        def publish(render_id: int) -> None:
            assert self.state.events[-1] == "commit"
            self.publication_commits.append(self.session.commits)
            self.queued.append(render_id)

        monkeypatch.setattr(render_endpoint.render_video, "delay", publish)
        monkeypatch.setattr(render_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(render_task, "ScriptRepository", InMemoryScriptRepository)
        monkeypatch.setattr(render_task, "VoiceTrackRepository", InMemoryVoiceTrackRepository)
        monkeypatch.setattr(render_task, "BrollCollectionRepository", InMemoryCollectionRepository)
        monkeypatch.setattr(render_task, "BrollAssetRepository", InMemoryAssetRepository)
        monkeypatch.setattr(render_task, "VideoRenderRepository", InMemoryRenderRepository)
        monkeypatch.setattr(render_task, "FFmpegVideoRenderer", lambda root: self.renderer)
        monkeypatch.setattr(
            render_task,
            "get_settings",
            lambda: SimpleNamespace(storage_root=self.storage_root),
        )

    def _service(self, renderer) -> VideoRenderService:
        return VideoRenderService(
            self.script_repository,
            self.voice_repository,
            self.collection_repository,
            self.asset_repository,
            self.render_repository,
            renderer,
        )

    def client(self) -> TestClient:
        return TestClient(self.app)

    def run_task(self, render_id: int) -> dict[str, int | str | None]:
        return asyncio.run(render_task._run_video_rendering(render_id))

    def artifact_path(self, render_id: int) -> Path:
        return self.storage_root / f"renders/{render_id}/output.mp4"


def render_request(*, broll_collection_id: int | None = None, subtitles: bool = True) -> dict:
    return {
        "voice_track_id": 8,
        "broll_collection_id": broll_collection_id,
        "options": {
            "resolution_preset": "custom",
            "width": 160,
            "height": 90,
            "fps": 12,
            "subtitle_enabled": subtitles,
            "normalize_audio": True,
        },
    }


def write_wav(path: Path, duration: float = 0.6, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_count = round(duration * sample_rate)
    samples = [int(2000 * ((index % 40) / 20 - 1)) for index in range(sample_count)]
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(struct.pack(f"<{sample_count}h", *samples))


def probe_streams(path: Path) -> set[str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return {stream["codec_type"] for stream in json.loads(result.stdout)["streams"]}


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg tooling is not installed")
def test_real_workflow_variants_broll_fallback_and_idempotency(tmp_path, monkeypatch) -> None:
    harness = Phase7Harness(monkeypatch, tmp_path)
    write_wav(tmp_path / harness.state.voice.storage_key)

    with harness.client() as client:
        first_response = client.post(
            "/api/v1/scripts/4/renders",
            json=render_request(broll_collection_id=12),
        )
        first = VideoRenderStatusResponse.model_validate(first_response.json())
        assert first_response.status_code == 202
        assert first.status == VideoRenderStatus.PENDING
        assert harness.queued == [first.id]
        assert harness.publication_commits == [1]
        assert harness.state.events[-1] == "commit"

        first_result = harness.run_task(harness.queued.pop())
        assert first_result == {
            "render_id": first.id,
            "render_status": "completed",
            "storage_key": f"renders/{first.id}/output.mp4",
        }
        first_row = harness.state.renders[first.id]
        first_path = harness.artifact_path(first.id)
        first_bytes = first_path.read_bytes()
        first_checksum = first_row.checksum
        assert first_row.status == VideoRenderStatus.COMPLETED
        assert first_row.file_size_bytes == len(first_bytes) > 0
        assert first_row.checksum == f"sha256:{hashlib.sha256(first_bytes).hexdigest()}"
        assert first_row.duration_seconds > 0
        assert probe_streams(first_path) >= {"video", "audio"}
        assert any(item["broll_asset_id"] == 21 for item in first_row.timeline_data)
        assert harness.renderer.inputs[0].selected_broll_assets[0].storage_key is None

        completed_response = client.get(f"/api/v1/renders/{first.id}")
        completed = VideoRenderResponse.model_validate(completed_response.json())
        assert completed_response.status_code == 200
        assert completed.storage_key == first_row.storage_key

        second_response = client.post(
            "/api/v1/scripts/4/renders",
            json=render_request(subtitles=False),
        )
        second = VideoRenderStatusResponse.model_validate(second_response.json())
        assert second_response.status_code == 202
        assert second.id != first.id
        assert len(harness.state.renders) == 2
        assert harness.state.renders[first.id].broll_collection_id == 12
        assert harness.state.renders[second.id].broll_collection_id is None
        assert harness.state.renders[first.id].render_options["subtitle_enabled"] is True
        assert harness.state.renders[second.id].render_options["subtitle_enabled"] is False

        mixed = client.get("/api/v1/scripts/4/renders")
        assert mixed.status_code == 200
        assert [item["id"] for item in mixed.json()] == [second.id, first.id]
        VideoRenderStatusResponse.model_validate(mixed.json()[0])
        VideoRenderResponse.model_validate(mixed.json()[1])

        harness.run_task(harness.queued.pop())
        second_path = harness.artifact_path(second.id)
        assert second_path.exists()
        assert second_path != first_path
        assert all(
            item["broll_asset_id"] is None
            for item in harness.state.renders[second.id].timeline_data
        )
        assert all(
            item["item_type"] != "subtitle"
            for item in harness.state.renders[second.id].timeline_data
        )

        calls_before_rerun = harness.renderer.calls
        repeated = harness.run_task(first.id)
        assert repeated["render_status"] == "completed"
        assert harness.renderer.calls == calls_before_rerun
        assert first_path.read_bytes() == first_bytes
        assert harness.state.renders[first.id].checksum == first_checksum

        queued_before_retry = list(harness.queued)
        retry = client.post(f"/api/v1/renders/{first.id}/retry")
        assert retry.status_code == 200
        VideoRenderResponse.model_validate(retry.json())
        assert harness.queued == queued_before_retry
        assert len(harness.state.renders) == 2


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg tooling is not installed")
def test_failed_render_retry_recovers_same_row(tmp_path, monkeypatch) -> None:
    harness = Phase7Harness(monkeypatch, tmp_path)
    write_wav(tmp_path / harness.state.voice.storage_key)

    with harness.client() as client:
        created = client.post("/api/v1/scripts/4/renders", json=render_request())
        render_id = VideoRenderStatusResponse.model_validate(created.json()).id
        harness.queued.clear()
        harness.renderer = FailingRenderer()

        with pytest.raises(VideoRenderingError):
            harness.run_task(render_id)

        failed_row = harness.state.renders[render_id]
        assert failed_row.status == VideoRenderStatus.FAILED
        assert failed_row.completed_at is None
        assert failed_row.error_message == "controlled renderer failure"
        assert harness.state.events[-1] == "commit"
        failed = VideoRenderStatusResponse.model_validate(
            client.get(f"/api/v1/renders/{render_id}").json()
        )
        assert failed.status == VideoRenderStatus.FAILED

        commits_before_retry = harness.session.commits
        retry_response = client.post(f"/api/v1/renders/{render_id}/retry")
        retry = VideoRenderStatusResponse.model_validate(retry_response.json())
        assert retry_response.status_code == 202
        assert retry.id == render_id
        assert retry.error_message is None
        assert harness.publication_commits[-1] == commits_before_retry + 1
        assert len(harness.state.renders) == 1

        harness.renderer = CountingFFmpegVideoRenderer(tmp_path)
        recovered = harness.run_task(harness.queued.pop())
        assert recovered["render_id"] == render_id
        assert recovered["render_status"] == "completed"
        assert harness.artifact_path(render_id).exists()
        assert len(harness.state.renders) == 1


def test_broker_failure_persists_same_row_without_renderer_execution(tmp_path, monkeypatch) -> None:
    harness = Phase7Harness(monkeypatch, tmp_path)

    def fail_publish(render_id: int) -> None:
        assert harness.state.events[-1] == "commit"
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(render_endpoint.render_video, "delay", fail_publish)
    with harness.client() as client:
        response = client.post("/api/v1/scripts/4/renders", json=render_request())

    assert response.status_code == 503
    assert response.json() == {"detail": "Video rendering could not be queued"}
    assert len(harness.state.renders) == 1
    failed = next(iter(harness.state.renders.values()))
    assert failed.status == VideoRenderStatus.FAILED
    assert failed.completed_at is None
    assert failed.error_message == "Video render task enqueue failed: broker unavailable"
    assert failed.storage_key is None
    assert harness.renderer.calls == 0
    assert harness.state.events[-2:] == [f"save:{failed.id}:failed", "commit"]
