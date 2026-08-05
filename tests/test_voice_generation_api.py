from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_voice_generation_service
from app.api.v1.endpoints import voice_tracks as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    ScriptNotFoundError,
    ScriptNotReadyError,
    UnusableScriptContentError,
    VoiceTrackNotFoundError,
)
from app.models.voice_track import VoiceTrackStatus


def voice_track(state: VoiceTrackStatus, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "script_id": 4,
        "status": state,
        "provider": "local",
        "voice": "default",
        "style": "neutral",
        "language": "en",
        "audio_format": "wav",
        "sample_rate_hz": 24000,
        "speaking_rate": 1.0,
        "pitch": 0.0,
        "volume_gain_db": 0.0,
        "generation_options": options(),
        "segments": [],
        "storage_key": None,
        "duration_seconds": None,
        "file_size_bytes": None,
        "checksum": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def completed_voice_track(**values):
    defaults = {
        "storage_key": "voice/1/audio.wav",
        "duration_seconds": 12.5,
        "file_size_bytes": 48044,
        "checksum": "sha256:abc123",
        "segments": [
            {
                "order": 0,
                "section_type": "full_script",
                "text": "Narration text",
                "audio_start_time": 0,
                "audio_end_time": 12.5,
                "source_script_section_order": None,
            }
        ],
        "completed_at": datetime.now(UTC),
    }
    defaults.update(values)
    return voice_track(VoiceTrackStatus.COMPLETED, **defaults)


class FakeService:
    def __init__(self, current=None, *, voice_tracks=None, should_enqueue=True) -> None:
        self.current = current
        self.voice_tracks = voice_tracks or []
        self.should_enqueue = should_enqueue
        self.created_options = None
        self.enqueue_failures = []

    async def request_voice_generation(self, script_id, generation_options):
        self.created_options = generation_options
        return self.current

    async def get_voice_track(self, voice_track_id):
        return self.current

    async def list_voice_tracks_for_script(self, script_id):
        return self.voice_tracks

    async def prepare_voice_track_retry(self, voice_track_id):
        if self.should_enqueue:
            self.current.status = VoiceTrackStatus.PENDING
            self.current.completed_at = None
            self.current.error_message = None
        return self.current, self.should_enqueue

    async def mark_voice_enqueue_failed(self, current, error):
        current.status = VoiceTrackStatus.FAILED
        current.completed_at = None
        current.error_message = f"Voice generation task enqueue failed: {error}"
        self.enqueue_failures.append((current, error))


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_voice_generation_service] = lambda: service
    return TestClient(app)


def options(**values) -> dict:
    result = {
        "provider": "local",
        "voice": "default",
        "style": "neutral",
        "language": "en",
        "audio_format": "wav",
        "sample_rate_hz": 24000,
        "speaking_rate": 1.0,
        "pitch": 0.0,
        "volume_gain_db": 0.0,
    }
    result.update(values)
    return result


def test_create_queues_pending_voice_track_and_returns_202(monkeypatch) -> None:
    current = voice_track(VoiceTrackStatus.PENDING, id=12)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_voice, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/4/voice-tracks", json=options())

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert service.created_options.sample_rate_hz == 24000
    assert queued == [12]


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ScriptNotFoundError(), 404),
        (ScriptNotReadyError(), 409),
        (UnusableScriptContentError(), 422),
    ],
)
def test_create_maps_script_precondition_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_voice_generation(self, script_id, generation_options):
            raise error

    with client_for(FailingService()) as client:
        response = client.post("/api/v1/scripts/4/voice-tracks", json=options())

    assert response.status_code == expected_status


def test_create_rejects_invalid_request_schema() -> None:
    with client_for(FakeService()) as client:
        response = client.post(
            "/api/v1/scripts/4/voice-tracks",
            json=options(sample_rate_hz=7999),
        )

    assert response.status_code == 422


def test_create_enqueue_failure_returns_503_and_persists_failure(monkeypatch) -> None:
    current = voice_track(VoiceTrackStatus.PENDING)
    service = FakeService(current)

    def fail(voice_track_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.generate_voice, "delay", fail)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/4/voice-tracks", json=options())

    assert response.status_code == 503
    assert response.json() == {"detail": "Voice generation could not be queued"}
    assert current.status == VoiceTrackStatus.FAILED
    assert current.completed_at is None
    assert current.error_message == "Voice generation task enqueue failed: broker unavailable"
    assert service.enqueue_failures[0][0] is current


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (VoiceTrackStatus.PENDING, None),
        (VoiceTrackStatus.GENERATING, None),
        (VoiceTrackStatus.FAILED, "Synthesis failed"),
    ],
)
def test_get_incomplete_voice_track_returns_status(state, message) -> None:
    current = voice_track(state, error_message=message)

    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/voice-tracks/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "script_id": 4,
        "status": state.value,
        "completed_at": None,
        "error_message": message,
    }


def test_get_completed_voice_track_returns_full_response() -> None:
    with client_for(FakeService(completed_voice_track())) as client:
        response = client.get("/api/v1/voice-tracks/1")

    body = response.json()
    assert response.status_code == 200
    assert body["storage_key"] == "voice/1/audio.wav"
    assert body["generation_options"]["provider"] == "local"
    assert body["segments"][0]["section_type"] == "full_script"
    assert body["checksum"] == "sha256:abc123"


def test_get_missing_voice_track_returns_404() -> None:
    class MissingService(FakeService):
        async def get_voice_track(self, voice_track_id):
            raise VoiceTrackNotFoundError

    with client_for(MissingService()) as client:
        response = client.get("/api/v1/voice-tracks/999")

    assert response.status_code == 404


def test_list_returns_mixed_variants_in_service_order() -> None:
    now = datetime.now(UTC)
    newest = voice_track(VoiceTrackStatus.PENDING, id=2, created_at=now)
    oldest = completed_voice_track(id=1, created_at=now - timedelta(minutes=1))

    with client_for(FakeService(voice_tracks=[newest, oldest])) as client:
        response = client.get("/api/v1/scripts/4/voice-tracks")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2, 1]
    assert "storage_key" not in response.json()[0]
    assert response.json()[1]["storage_key"] == "voice/1/audio.wav"


def test_list_allows_empty_result() -> None:
    with client_for(FakeService(voice_tracks=[])) as client:
        response = client.get("/api/v1/scripts/4/voice-tracks")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("state", [VoiceTrackStatus.PENDING, VoiceTrackStatus.FAILED])
def test_retry_reenqueues_same_row_and_clears_error(monkeypatch, state) -> None:
    current = voice_track(state, id=9, error_message="Old error")
    service = FakeService(current, should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_voice, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/voice-tracks/9/retry")

    assert response.status_code == 202
    assert response.json()["id"] == 9
    assert response.json()["status"] == "pending"
    assert current.error_message is None
    assert queued == [9]


@pytest.mark.parametrize("state", [VoiceTrackStatus.GENERATING, VoiceTrackStatus.COMPLETED])
def test_retry_does_not_enqueue_active_or_completed_track(monkeypatch, state) -> None:
    current = completed_voice_track() if state == VoiceTrackStatus.COMPLETED else voice_track(state)
    service = FakeService(current, should_enqueue=False)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_voice, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/voice-tracks/1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert queued == []


def test_retry_enqueue_failure_returns_503(monkeypatch) -> None:
    current = voice_track(VoiceTrackStatus.FAILED, error_message="Old error")
    service = FakeService(current, should_enqueue=True)

    def fail(voice_track_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.generate_voice, "delay", fail)

    with client_for(service) as client:
        response = client.post("/api/v1/voice-tracks/1/retry")

    assert response.status_code == 503
    assert current.status == VoiceTrackStatus.FAILED
    assert current.error_message == "Voice generation task enqueue failed: broker unavailable"
