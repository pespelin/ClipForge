from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.core.exceptions import VoiceGenerationError, VoiceTrackNotFoundError
from app.models.voice_track import (
    AudioFormat,
    VoiceStyle,
    VoiceTrack,
    VoiceTrackStatus,
)
from app.schemas.voice_track import VoiceGenerationOptions
from app.services.voice_generation_service import VoiceGenerationService
from app.tasks import voice_generation as task_module


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


async def test_task_composes_dependencies_storage_and_returns_result(monkeypatch, tmp_path) -> None:
    session = FakeSession()
    dependencies = {}

    class FakeProvider:
        def __init__(self, storage_root) -> None:
            dependencies["storage_root"] = storage_root

    class FakeService:
        def __init__(self, script_repository, voice_track_repository, tts_provider) -> None:
            dependencies.update(
                script_repository=script_repository,
                voice_track_repository=voice_track_repository,
                tts_provider=tts_provider,
            )

        async def process_voice_track(self, voice_track_id: int):
            dependencies["voice_track_id"] = voice_track_id
            return SimpleNamespace(
                id=voice_track_id,
                status=VoiceTrackStatus.COMPLETED,
                storage_key=f"voice/{voice_track_id}/audio.wav",
            )

    script_repository = object()
    voice_track_repository = object()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: script_repository)
    monkeypatch.setattr(
        task_module, "VoiceTrackRepository", lambda received: voice_track_repository
    )
    monkeypatch.setattr(task_module, "LocalTTSProvider", FakeProvider)
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VoiceGenerationService", FakeService)

    result = await task_module._run_voice_generation(9)

    assert result == {
        "voice_track_id": 9,
        "voice_track_status": "completed",
        "storage_key": "voice/9/audio.wav",
    }
    assert dependencies == {
        "storage_root": tmp_path,
        "script_repository": script_repository,
        "voice_track_repository": voice_track_repository,
        "tts_provider": dependencies["tts_provider"],
        "voice_track_id": 9,
    }
    assert isinstance(dependencies["tts_provider"], FakeProvider)
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_sync_task_runs_async_helper_and_is_registered(monkeypatch) -> None:
    async def fake_run(voice_track_id: int) -> dict[str, int | str | None]:
        return {
            "voice_track_id": voice_track_id,
            "voice_track_status": "completed",
            "storage_key": f"voice/{voice_track_id}/audio.wav",
        }

    monkeypatch.setattr(task_module, "_run_voice_generation", fake_run)

    assert task_module.generate_voice.run(7) == {
        "voice_track_id": 7,
        "voice_track_status": "completed",
        "storage_key": "voice/7/audio.wav",
    }
    assert task_module.generate_voice.name == "voice.generate"
    assert task_module.celery_app.tasks["voice.generate"].name == "voice.generate"


async def test_voice_generation_error_commits_failed_state_and_reraises(
    monkeypatch, tmp_path
) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_voice_track(self, voice_track_id: int):
            raise VoiceGenerationError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VoiceTrackRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalTTSProvider", lambda root: object())
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VoiceGenerationService", FailingService)

    with pytest.raises(VoiceGenerationError):
        await task_module._run_voice_generation(7)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_precondition_error_rolls_back_and_reraises(monkeypatch, tmp_path) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_voice_track(self, voice_track_id: int):
            raise VoiceTrackNotFoundError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VoiceTrackRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalTTSProvider", lambda root: object())
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VoiceGenerationService", FailingService)

    with pytest.raises(VoiceTrackNotFoundError):
        await task_module._run_voice_generation(7)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


async def test_completed_track_task_is_idempotent(monkeypatch, tmp_path) -> None:
    session = FakeSession()
    completed = VoiceTrack(
        id=7,
        script_id=4,
        status=VoiceTrackStatus.COMPLETED,
        provider="local",
        voice="default",
        style=VoiceStyle.NEUTRAL,
        language="en",
        audio_format=AudioFormat.WAV,
        sample_rate_hz=24000,
        speaking_rate=1,
        pitch=0,
        volume_gain_db=0,
        generation_options=VoiceGenerationOptions().model_dump(mode="json"),
        storage_key="voice/7/audio.wav",
        duration_seconds=1,
        file_size_bytes=100,
    )

    class CompletedTrackRepository:
        async def get(self, voice_track_id: int) -> VoiceTrack | None:
            return completed if voice_track_id == completed.id else None

    class UnexpectedScriptRepository:
        async def get(self, script_id: int):
            raise AssertionError("completed tracks must not load their script")

    class CountingProvider:
        def __init__(self, storage_root) -> None:
            self.calls = 0

        async def synthesize(self, synthesis_input):
            self.calls += 1
            raise AssertionError("completed tracks must not invoke TTS")

    provider = CountingProvider(tmp_path)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        task_module, "ScriptRepository", lambda received: UnexpectedScriptRepository()
    )
    monkeypatch.setattr(
        task_module, "VoiceTrackRepository", lambda received: CompletedTrackRepository()
    )
    monkeypatch.setattr(task_module, "LocalTTSProvider", lambda root: provider)
    monkeypatch.setattr(task_module, "get_settings", lambda: SimpleNamespace(storage_root=tmp_path))
    monkeypatch.setattr(task_module, "VoiceGenerationService", VoiceGenerationService)

    result = await task_module._run_voice_generation(7)

    assert result == {
        "voice_track_id": 7,
        "voice_track_status": "completed",
        "storage_key": "voice/7/audio.wav",
    }
    assert provider.calls == 0
    assert session.commits == 1
    assert session.closed


def test_operational_error_uses_bounded_celery_retry(monkeypatch) -> None:
    async def fail_with_operational_error(voice_track_id: int):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(task_module, "_run_voice_generation", fail_with_operational_error)
    monkeypatch.setattr(task_module.generate_voice, "retry", retry)

    with pytest.raises(Retry):
        task_module.generate_voice.run(7)

    retry.assert_called_once()
    assert task_module.generate_voice.autoretry_for == (OperationalError,)
    assert task_module.generate_voice.retry_backoff is True
    assert task_module.generate_voice.max_retries == 3
