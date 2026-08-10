from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies as dependency_module
from app.api.dependencies import get_video_render_service
from app.api.v1.endpoints import video_renders as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    BrollCollectionNotFoundError,
    RenderBrollCollectionMismatchError,
    RenderBrollCollectionNotReadyError,
    RenderScriptNotReadyError,
    RenderVoiceTrackMismatchError,
    RenderVoiceTrackNotReadyError,
    ScriptNotFoundError,
    UnusableVideoRenderInputError,
    VideoRenderNotFoundError,
    VoiceTrackNotFoundError,
)
from app.models.video_render import VideoRenderStatus


def request_body(**values) -> dict:
    result = {"voice_track_id": 8}
    result.update(values)
    return result


def video_render(state: VideoRenderStatus, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "script_id": 4,
        "voice_track_id": 8,
        "broll_collection_id": None,
        "status": state,
        "completed_at": None,
        "error_message": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def completed_render(**values):
    defaults = {
        "output_format": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "resolution_preset": "vertical_1080x1920",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "fit_mode": "cover",
        "background_color": "#000000",
        "subtitle_enabled": True,
        "subtitle_style": {
            "font_family": "Arial",
            "font_size": 48,
            "font_weight": 700,
            "text_color": "#FFFFFF",
            "background_color": "#000000",
            "position": "bottom",
            "max_chars_per_line": 32,
            "stroke_width": 2,
            "stroke_color": "#000000",
        },
        "render_options": {
            "output_format": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
            "resolution_preset": "vertical_1080x1920",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "fit_mode": "cover",
            "background_color": "#000000",
            "subtitle_enabled": True,
            "subtitle_style": {
                "font_family": "Arial",
                "font_size": 48,
                "font_weight": 700,
                "text_color": "#FFFFFF",
                "background_color": "#000000",
                "position": "bottom",
                "max_chars_per_line": 32,
                "stroke_width": 2,
                "stroke_color": "#000000",
            },
            "normalize_audio": True,
            "target_loudness_lufs": -14,
            "include_broll": True,
            "include_original_video": False,
            "watermark_text": None,
        },
        "timeline_data": [
            {
                "order": 0,
                "item_type": "narration",
                "source_storage_key": "voice/8/audio.wav",
                "source_start_time": 0,
                "source_end_time": 10,
                "timeline_start_time": 0,
                "timeline_end_time": 10,
            }
        ],
        "storage_key": "renders/1/output.mp4",
        "duration_seconds": 10,
        "file_size_bytes": 4096,
        "checksum": "sha256:abc",
        "completed_at": datetime.now(UTC),
    }
    defaults.update(values)
    return video_render(VideoRenderStatus.COMPLETED, **defaults)


class FakeService:
    def __init__(self, current=None, *, renders=None, should_enqueue=True) -> None:
        self.current = current
        self.renders = renders or []
        self.should_enqueue = should_enqueue
        self.created = []
        self.enqueue_failures = []

    async def request_video_render(self, script_id, voice_track_id, broll_collection_id, options):
        self.created.append((script_id, voice_track_id, broll_collection_id, options))
        if self.current is None:
            self.current = video_render(
                VideoRenderStatus.PENDING, id=len(self.renders) + 1, script_id=script_id
            )
            self.renders.insert(0, self.current)
        return self.current

    async def list_renders_for_script(self, script_id):
        return self.renders

    async def get_render(self, render_id):
        return self.current

    async def prepare_render_retry(self, render_id):
        if self.should_enqueue:
            self.current.status = VideoRenderStatus.PENDING
            self.current.completed_at = None
            self.current.error_message = None
        return self.current, self.should_enqueue

    async def mark_render_enqueue_failed(self, current, error):
        current.status = VideoRenderStatus.FAILED
        current.completed_at = None
        current.error_message = f"Video render task enqueue failed: {error}"
        self.enqueue_failures.append((current, error))


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_video_render_service] = lambda: service
    return TestClient(app)


def test_create_queues_pending_render_and_returns_202(monkeypatch) -> None:
    current = video_render(VideoRenderStatus.PENDING, id=12)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.render_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post(
            "/api/v1/scripts/4/renders",
            json=request_body(broll_collection_id=7),
        )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert service.created[0][:3] == (4, 8, 7)
    assert service.created[0][3].output_format.value == "mp4"
    assert queued == [12]


def test_multiple_explicit_creates_are_separate_variants(monkeypatch) -> None:
    service = FakeService()
    queued = []
    monkeypatch.setattr(endpoint_module.render_video, "delay", queued.append)

    with client_for(service) as client:
        first = client.post("/api/v1/scripts/4/renders", json=request_body())
        service.current = None
        second = client.post(
            "/api/v1/scripts/4/renders",
            json=request_body(options={"subtitle_enabled": False}),
        )

    assert first.json()["id"] != second.json()["id"]
    assert len(service.renders) == 2
    assert queued == [1, 2]


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ScriptNotFoundError(), 404),
        (RenderScriptNotReadyError(), 409),
        (VoiceTrackNotFoundError(), 404),
        (RenderVoiceTrackNotReadyError(), 409),
        (RenderVoiceTrackMismatchError(), 422),
        (BrollCollectionNotFoundError(), 404),
        (RenderBrollCollectionNotReadyError(), 409),
        (RenderBrollCollectionMismatchError(), 422),
        (UnusableVideoRenderInputError(), 422),
    ],
)
def test_create_maps_application_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_video_render(
            self, script_id, voice_track_id, broll_collection_id, options
        ):
            raise error

    with client_for(FailingService()) as client:
        response = client.post("/api/v1/scripts/4/renders", json=request_body())

    assert response.status_code == expected_status


def test_create_rejects_invalid_request() -> None:
    with client_for(FakeService()) as client:
        response = client.post("/api/v1/scripts/4/renders", json=request_body(voice_track_id=0))

    assert response.status_code == 422


def test_create_enqueue_failure_returns_503_and_persists_failure(monkeypatch) -> None:
    current = video_render(VideoRenderStatus.PENDING)
    service = FakeService(current)

    def fail(render_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.render_video, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/scripts/4/renders", json=request_body())

    assert response.status_code == 503
    assert response.json() == {"detail": "Video rendering could not be queued"}
    assert current.status == VideoRenderStatus.FAILED
    assert current.completed_at is None
    assert current.error_message == "Video render task enqueue failed: broker unavailable"


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (VideoRenderStatus.PENDING, None),
        (VideoRenderStatus.RENDERING, None),
        (VideoRenderStatus.FAILED, "FFmpeg failed"),
    ],
)
def test_get_noncompleted_render_returns_status(state, message) -> None:
    current = video_render(state, error_message=message)
    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/renders/1")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert response.json()["error_message"] == message
    assert "storage_key" not in response.json()


def test_get_completed_render_returns_full_response() -> None:
    with client_for(FakeService(completed_render())) as client:
        response = client.get("/api/v1/renders/1")

    assert response.status_code == 200
    assert response.json()["storage_key"] == "renders/1/output.mp4"
    assert response.json()["timeline_data"][0]["item_type"] == "narration"
    assert response.json()["checksum"] == "sha256:abc"


def test_get_missing_render_returns_404() -> None:
    class MissingService(FakeService):
        async def get_render(self, render_id):
            raise VideoRenderNotFoundError

    with client_for(MissingService()) as client:
        response = client.get("/api/v1/renders/999")

    assert response.status_code == 404


def test_list_returns_mixed_renders_in_service_order() -> None:
    now = datetime.now(UTC)
    newest = video_render(VideoRenderStatus.PENDING, id=2, created_at=now)
    oldest = completed_render(id=1, created_at=now - timedelta(minutes=1))
    with client_for(FakeService(renders=[newest, oldest])) as client:
        response = client.get("/api/v1/scripts/4/renders")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2, 1]
    assert "storage_key" not in response.json()[0]
    assert response.json()[1]["storage_key"] == "renders/1/output.mp4"


def test_list_allows_empty_result() -> None:
    with client_for(FakeService(renders=[])) as client:
        response = client.get("/api/v1/scripts/4/renders")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("state", [VideoRenderStatus.PENDING, VideoRenderStatus.FAILED])
def test_retry_reenqueues_same_row_and_clears_error(monkeypatch, state) -> None:
    current = video_render(state, id=9, error_message="Old error")
    service = FakeService(current, renders=[current], should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.render_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/renders/9/retry")

    assert response.status_code == 202
    assert response.json()["id"] == 9
    assert current.error_message is None
    assert queued == [9]
    assert service.renders == [current]


@pytest.mark.parametrize("state", [VideoRenderStatus.RENDERING, VideoRenderStatus.COMPLETED])
def test_retry_does_not_enqueue_active_or_completed_render(monkeypatch, state) -> None:
    current = completed_render() if state == VideoRenderStatus.COMPLETED else video_render(state)
    service = FakeService(current, should_enqueue=False)
    queued = []
    monkeypatch.setattr(endpoint_module.render_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/renders/1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert queued == []


def test_retry_enqueue_failure_returns_503(monkeypatch) -> None:
    current = video_render(VideoRenderStatus.FAILED, error_message="Old error")
    service = FakeService(current)

    def fail(render_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.render_video, "delay", fail)
    with client_for(service) as client:
        response = client.post("/api/v1/renders/1/retry")

    assert response.status_code == 503
    assert current.status == VideoRenderStatus.FAILED
    assert current.error_message == "Video render task enqueue failed: broker unavailable"


def test_dependency_factory_composes_repositories_and_storage_renderer(monkeypatch) -> None:
    session = object()
    repositories = [object() for _ in range(5)]
    renderer = object()
    storage_root = object()

    monkeypatch.setattr(dependency_module, "ScriptRepository", lambda received: repositories[0])
    monkeypatch.setattr(dependency_module, "VoiceTrackRepository", lambda received: repositories[1])
    monkeypatch.setattr(
        dependency_module, "BrollCollectionRepository", lambda received: repositories[2]
    )
    monkeypatch.setattr(dependency_module, "BrollAssetRepository", lambda received: repositories[3])
    monkeypatch.setattr(
        dependency_module, "VideoRenderRepository", lambda received: repositories[4]
    )

    def renderer_factory(received_root):
        assert received_root is storage_root
        return renderer

    monkeypatch.setattr(dependency_module, "FFmpegVideoRenderer", renderer_factory)
    monkeypatch.setattr(
        dependency_module, "get_settings", lambda: SimpleNamespace(storage_root=storage_root)
    )

    service = dependency_module.get_video_render_service(session)

    assert service.script_repository is repositories[0]
    assert service.voice_track_repository is repositories[1]
    assert service.collection_repository is repositories[2]
    assert service.asset_repository is repositories[3]
    assert service.render_repository is repositories[4]
    assert service.renderer is renderer
