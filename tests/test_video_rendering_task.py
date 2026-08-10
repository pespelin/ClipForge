import hashlib
import inspect
import shutil
import struct
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.core.exceptions import VideoRenderingError, VideoRenderNotFoundError
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video_render import VideoRender, VideoRenderStatus
from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrack, VoiceTrackStatus
from app.schemas.video_render import RenderOptions
from app.services.video_render_service import VideoRenderService
from app.tasks import video_rendering as task_module


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def patch_dependencies(monkeypatch, session: FakeSession, service_type: type) -> None:
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VoiceTrackRepository", lambda received: object())
    monkeypatch.setattr(task_module, "BrollCollectionRepository", lambda received: object())
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoRenderRepository", lambda received: object())
    monkeypatch.setattr(task_module, "FFmpegVideoRenderer", lambda root: object())
    monkeypatch.setattr(
        task_module, "get_settings", lambda: SimpleNamespace(storage_root="storage-root")
    )
    monkeypatch.setattr(task_module, "VideoRenderService", service_type)


async def test_task_composes_dependencies_storage_and_returns_result(monkeypatch, tmp_path) -> None:
    session = FakeSession()
    dependencies = {}
    repositories = [object() for _ in range(5)]

    class FakeRenderer:
        def __init__(self, storage_root) -> None:
            dependencies["storage_root"] = storage_root

    class FakeService:
        def __init__(self, **values) -> None:
            dependencies.update(values)

        async def process_render(self, render_id: int):
            dependencies["render_id"] = render_id
            return SimpleNamespace(
                id=render_id,
                status=VideoRenderStatus.COMPLETED,
                storage_key=f"renders/{render_id}/output.mp4",
            )

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: repositories[0])
    monkeypatch.setattr(task_module, "VoiceTrackRepository", lambda received: repositories[1])
    monkeypatch.setattr(task_module, "BrollCollectionRepository", lambda received: repositories[2])
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: repositories[3])
    monkeypatch.setattr(task_module, "VideoRenderRepository", lambda received: repositories[4])
    monkeypatch.setattr(task_module, "FFmpegVideoRenderer", FakeRenderer)
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VideoRenderService", FakeService)

    result = await task_module._run_video_rendering(7)

    assert result == {
        "render_id": 7,
        "render_status": "completed",
        "storage_key": "renders/7/output.mp4",
    }
    assert dependencies["storage_root"] == tmp_path
    assert dependencies["script_repository"] is repositories[0]
    assert dependencies["voice_track_repository"] is repositories[1]
    assert dependencies["collection_repository"] is repositories[2]
    assert dependencies["asset_repository"] is repositories[3]
    assert dependencies["render_repository"] is repositories[4]
    assert dependencies["render_id"] == 7
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_sync_entrypoint_runs_async_helper_and_is_registered(monkeypatch) -> None:
    async def fake_run(render_id: int) -> dict[str, int | str | None]:
        return {
            "render_id": render_id,
            "render_status": "completed",
            "storage_key": f"renders/{render_id}/output.mp4",
        }

    monkeypatch.setattr(task_module, "_run_video_rendering", fake_run)

    assert task_module.render_video.run(7)["storage_key"] == "renders/7/output.mp4"
    assert task_module.render_video.name == "video.render"
    assert task_module.celery_app.tasks["video.render"].name == "video.render"
    assert "app.tasks.video_rendering" in task_module.celery_app.conf.include


async def test_rendering_error_commits_failed_state_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_render(self, render_id: int):
            raise VideoRenderingError

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(VideoRenderingError):
        await task_module._run_video_rendering(7)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_precondition_error_rolls_back_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_render(self, render_id: int):
            raise VideoRenderNotFoundError

    patch_dependencies(monkeypatch, session, FailingService)

    with pytest.raises(VideoRenderNotFoundError):
        await task_module._run_video_rendering(7)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


async def test_completed_render_is_idempotent_and_keeps_metadata(monkeypatch, tmp_path) -> None:
    session = FakeSession()
    completed = VideoRender(
        id=7,
        script_id=4,
        voice_track_id=8,
        status=VideoRenderStatus.COMPLETED,
        storage_key="renders/7/output.mp4",
        duration_seconds=1,
        file_size_bytes=100,
        checksum="sha256:existing",
    )

    class CompletedRepository:
        async def get(self, render_id: int):
            return completed

    class UnexpectedRepository:
        async def get(self, row_id: int):
            raise AssertionError("completed render inputs must not be loaded")

    class CountingRenderer:
        def __init__(self) -> None:
            self.calls = 0

        async def render(self, render_input):
            self.calls += 1
            raise AssertionError("completed renders must not run FFmpeg")

    renderer = CountingRenderer()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: UnexpectedRepository())
    monkeypatch.setattr(
        task_module, "VoiceTrackRepository", lambda received: UnexpectedRepository()
    )
    monkeypatch.setattr(
        task_module, "BrollCollectionRepository", lambda received: UnexpectedRepository()
    )
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: object())
    monkeypatch.setattr(
        task_module, "VideoRenderRepository", lambda received: CompletedRepository()
    )
    monkeypatch.setattr(task_module, "FFmpegVideoRenderer", lambda root: renderer)
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VideoRenderService", VideoRenderService)

    result = await task_module._run_video_rendering(7)

    assert result["render_status"] == "completed"
    assert renderer.calls == 0
    assert completed.checksum == "sha256:existing"
    assert completed.storage_key == "renders/7/output.mp4"
    assert session.commits == 1
    assert session.closed


def test_operational_error_uses_bounded_celery_retry(monkeypatch) -> None:
    async def fail_with_operational_error(render_id: int):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(task_module, "_run_video_rendering", fail_with_operational_error)
    monkeypatch.setattr(task_module.render_video, "retry", retry)

    with pytest.raises(Retry):
        task_module.render_video.run(7)

    retry.assert_called_once()
    assert task_module.render_video.autoretry_for == (OperationalError,)
    assert task_module.render_video.retry_backoff is True
    assert task_module.render_video.max_retries == 3


def test_task_contains_only_composition_and_transaction_boundary() -> None:
    source = inspect.getsource(task_module._run_video_rendering)
    assert "process_render" in source
    assert "subprocess" not in source
    assert "_build_timeline" not in source
    assert "checksum =" not in source
    assert "file_size" not in source


class IntegrationRepository:
    def __init__(self, row=None) -> None:
        self.row = row
        self.saved_statuses = []

    async def get(self, row_id: int):
        return self.row if self.row is not None and self.row.id == row_id else None

    async def save(self, row):
        self.row = row
        self.saved_statuses.append(row.status)
        return row


def write_wav(path, duration: float = 0.4, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = round(duration * sample_rate)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(struct.pack(f"<{samples}h", *([0] * samples)))


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg tooling is not installed",
)
async def test_real_task_helper_renders_and_commits_tiny_mp4(monkeypatch, tmp_path) -> None:
    session = FakeSession()
    script = Script(
        id=4,
        video_id="video-1",
        video_analysis_id=7,
        status=ScriptStatus.COMPLETED,
        title="Tiny render",
        hook="Render this.",
        body="Keep it short.",
        full_script="Render this. Keep it short.",
        target_duration_seconds=1,
        tone=ScriptTone.NEUTRAL,
        language="en",
        generation_options={},
        sections=[],
    )
    voice = VoiceTrack(
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
        segments=[],
        storage_key="voice/8/audio.wav",
        duration_seconds=0.4,
        file_size_bytes=1,
    )
    options = RenderOptions(
        resolution_preset="custom",
        width=160,
        height=90,
        fps=12,
        subtitle_enabled=False,
    )
    video_render = VideoRender(
        id=7,
        script_id=4,
        voice_track_id=8,
        broll_collection_id=None,
        status=VideoRenderStatus.PENDING,
        output_format=options.output_format,
        video_codec=options.video_codec,
        audio_codec=options.audio_codec,
        resolution_preset=options.resolution_preset,
        width=options.width,
        height=options.height,
        fps=options.fps,
        fit_mode=options.fit_mode,
        background_color=options.background_color,
        subtitle_enabled=options.subtitle_enabled,
        subtitle_style=options.subtitle_style.model_dump(mode="json"),
        render_options=options.model_dump(mode="json"),
        timeline_data=[],
    )
    script_repo = IntegrationRepository(script)
    voice_repo = IntegrationRepository(voice)
    render_repo = IntegrationRepository(video_render)
    write_wav(tmp_path / voice.storage_key)

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: script_repo)
    monkeypatch.setattr(task_module, "VoiceTrackRepository", lambda received: voice_repo)
    monkeypatch.setattr(
        task_module, "BrollCollectionRepository", lambda received: IntegrationRepository()
    )
    monkeypatch.setattr(task_module, "BrollAssetRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoRenderRepository", lambda received: render_repo)
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))

    result = await task_module._run_video_rendering(7)
    output = tmp_path / "renders/7/output.mp4"

    assert result == {
        "render_id": 7,
        "render_status": "completed",
        "storage_key": "renders/7/output.mp4",
    }
    assert output.is_file()
    assert video_render.file_size_bytes == output.stat().st_size
    assert video_render.checksum == f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"
    assert video_render.duration_seconds > 0
    assert render_repo.saved_statuses == [
        VideoRenderStatus.RENDERING,
        VideoRenderStatus.COMPLETED,
    ]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed
