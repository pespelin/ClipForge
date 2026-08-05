import asyncio
import hashlib
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_voice_generation_service
from app.api.v1.endpoints import voice_tracks as voice_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import VoiceGenerationError
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.voice_track import VoiceTrack, VoiceTrackStatus
from app.providers.tts import LocalTTSProvider
from app.schemas.voice_track import VoiceTrackResponse, VoiceTrackStatusResponse
from app.services.voice_generation_service import VoiceGenerationService
from app.tasks import voice_generation as voice_task


class WorkflowState:
    def __init__(self) -> None:
        self.script = Script(
            id=4,
            video_id="video-1",
            video_analysis_id=7,
            status=ScriptStatus.COMPLETED,
            title="Editing lessons",
            hook="Start with your strongest visual.",
            body="Remove every unnecessary pause.",
            full_script="Start with your strongest visual. Remove every unnecessary pause.",
            estimated_duration_seconds=10,
            target_duration_seconds=30,
            tone=ScriptTone.EDUCATIONAL,
            language="en",
            generation_options={
                "target_duration_seconds": 30,
                "tone": "educational",
                "language": "en",
                "include_call_to_action": False,
                "preferred_hook_candidate_index": None,
                "preferred_clip_candidate_index": None,
            },
            sections=[
                {
                    "order": 0,
                    "type": "hook",
                    "text": "Start with your strongest visual.",
                    "estimated_duration_seconds": 3,
                    "source_start_time": 0,
                    "source_end_time": 3,
                },
                {
                    "order": 1,
                    "type": "body",
                    "text": "Remove every unnecessary pause.",
                    "estimated_duration_seconds": 5,
                    "source_start_time": 3,
                    "source_end_time": 10,
                },
            ],
            completed_at=datetime.now(UTC),
        )
        self.voice_tracks: dict[int, VoiceTrack] = {}
        self.next_voice_track_id = 1
        self.events: list[str] = []
        self.saved_statuses: list[tuple[int, VoiceTrackStatus]] = []


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
        self.session = session
        self.state = session.state

    async def create(self, voice_track: VoiceTrack) -> VoiceTrack:
        voice_track.id = self.state.next_voice_track_id
        self.state.next_voice_track_id += 1
        timestamp = datetime.now(UTC)
        voice_track.created_at = timestamp
        voice_track.updated_at = timestamp
        self.state.voice_tracks[voice_track.id] = voice_track
        self.state.events.append(f"create:{voice_track.id}")
        return voice_track

    async def get(self, voice_track_id: int) -> VoiceTrack | None:
        return self.state.voice_tracks.get(voice_track_id)

    async def get_by_script_id(self, script_id: int) -> list[VoiceTrack]:
        rows = [row for row in self.state.voice_tracks.values() if row.script_id == script_id]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    async def save(self, voice_track: VoiceTrack) -> VoiceTrack:
        voice_track.updated_at = datetime.now(UTC)
        self.state.voice_tracks[voice_track.id] = voice_track
        self.state.saved_statuses.append((voice_track.id, voice_track.status))
        self.state.events.append(f"save:{voice_track.id}:{voice_track.status.value}")
        return voice_track

    async def commit(self) -> None:
        await self.session.commit()


class CountingLocalTTSProvider(LocalTTSProvider):
    def __init__(self, storage_root: Path) -> None:
        super().__init__(storage_root)
        self.calls = 0

    async def synthesize(self, synthesis_input):
        self.calls += 1
        return await super().synthesize(synthesis_input)


class FailingTTSProvider:
    async def synthesize(self, synthesis_input):
        raise RuntimeError("local TTS failed")


class Phase5Harness:
    def __init__(self, monkeypatch, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.state = WorkflowState()
        self.session = InMemorySession(self.state)
        self.provider = CountingLocalTTSProvider(storage_root)
        self.queued: list[int] = []
        self.publication_commits: list[int] = []
        self.publication_states: list[VoiceTrackStatus] = []
        self.script_repository = InMemoryScriptRepository(self.session)
        self.voice_track_repository = InMemoryVoiceTrackRepository(self.session)
        self.service = self._service(self.provider)

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_voice_generation_service] = lambda: self.service

        def publish(voice_track_id: int) -> None:
            assert self.state.events[-1] == "commit"
            self.publication_commits.append(self.session.commits)
            self.publication_states.append(self.state.voice_tracks[voice_track_id].status)
            self.queued.append(voice_track_id)

        monkeypatch.setattr(voice_endpoint.generate_voice, "delay", publish)
        monkeypatch.setattr(voice_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(voice_task, "ScriptRepository", InMemoryScriptRepository)
        monkeypatch.setattr(voice_task, "VoiceTrackRepository", InMemoryVoiceTrackRepository)
        monkeypatch.setattr(voice_task, "LocalTTSProvider", lambda root: self.provider)
        monkeypatch.setattr(
            voice_task,
            "get_settings",
            lambda: SimpleNamespace(storage_root=self.storage_root),
        )

    def _service(self, provider) -> VoiceGenerationService:
        return VoiceGenerationService(
            self.script_repository,
            self.voice_track_repository,
            provider,
        )

    def client(self) -> TestClient:
        return TestClient(self.app)

    def run_task(self, voice_track_id: int) -> dict[str, int | str | None]:
        return asyncio.run(voice_task._run_voice_generation(voice_track_id))

    def artifact_path(self, voice_track_id: int) -> Path:
        return self.storage_root / f"voice/{voice_track_id}/audio.wav"


def request_options(**values) -> dict:
    result = {
        "provider": "local",
        "voice": "default",
        "style": "neutral",
        "language": "en",
        "audio_format": "wav",
        "sample_rate_hz": 16000,
        "speaking_rate": 1.0,
        "pitch": 0.0,
        "volume_gain_db": 0.0,
    }
    result.update(values)
    return result


def test_phase4_to_phase5_workflow_variants_artifacts_and_idempotency(
    tmp_path: Path, monkeypatch
) -> None:
    harness = Phase5Harness(monkeypatch, tmp_path)

    with harness.client() as client:
        first_response = client.post("/api/v1/scripts/4/voice-tracks", json=request_options())
        assert first_response.status_code == 202
        first_pending = VoiceTrackStatusResponse.model_validate(first_response.json())
        assert first_pending.status == VoiceTrackStatus.PENDING
        assert harness.queued == [first_pending.id]
        assert harness.publication_commits == [1]
        assert harness.publication_states == [VoiceTrackStatus.PENDING]

        first_task_result = harness.run_task(harness.queued.pop())
        assert first_task_result == {
            "voice_track_id": first_pending.id,
            "voice_track_status": "completed",
            "storage_key": f"voice/{first_pending.id}/audio.wav",
        }

        first_row = harness.state.voice_tracks[first_pending.id]
        first_path = harness.storage_root / first_row.storage_key
        first_bytes = first_path.read_bytes()
        first_checksum = first_row.checksum
        assert first_path == harness.artifact_path(first_pending.id)
        assert first_row.file_size_bytes == len(first_bytes)
        assert first_row.checksum == f"sha256:{hashlib.sha256(first_bytes).hexdigest()}"
        assert first_row.duration_seconds > 0
        with wave.open(str(first_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 16000
            assert wav_file.getnframes() > 0
            assert any(wav_file.readframes(wav_file.getnframes()))

        completed_response = client.get(f"/api/v1/voice-tracks/{first_pending.id}")
        first_completed = VoiceTrackResponse.model_validate(completed_response.json())
        assert completed_response.status_code == 200
        assert first_completed.status == VoiceTrackStatus.COMPLETED
        assert first_completed.storage_key == first_row.storage_key
        assert first_completed.segments

        second_response = client.post(
            "/api/v1/scripts/4/voice-tracks",
            json=request_options(
                voice="alternate",
                style="energetic",
                speaking_rate=1.5,
            ),
        )
        second_pending = VoiceTrackStatusResponse.model_validate(second_response.json())
        assert second_response.status_code == 202
        assert second_pending.id != first_pending.id
        assert len(harness.state.voice_tracks) == 2

        second_row = harness.state.voice_tracks[second_pending.id]
        assert first_row.script_id == second_row.script_id == 4
        assert first_row.generation_options["voice"] == "default"
        assert second_row.generation_options["voice"] == "alternate"
        assert first_row.generation_options["speaking_rate"] == 1.0
        assert second_row.generation_options["speaking_rate"] == 1.5

        mixed_response = client.get("/api/v1/scripts/4/voice-tracks")
        mixed = mixed_response.json()
        assert mixed_response.status_code == 200
        assert [item["id"] for item in mixed] == [second_pending.id, first_pending.id]
        VoiceTrackStatusResponse.model_validate(mixed[0])
        VoiceTrackResponse.model_validate(mixed[1])

        harness.run_task(harness.queued.pop())
        second_path = harness.artifact_path(second_pending.id)
        assert second_path.exists()
        assert second_path != first_path
        assert first_path.read_bytes() == first_bytes
        assert harness.provider.calls == 2

        repeated_result = harness.run_task(first_pending.id)
        assert repeated_result["voice_track_status"] == "completed"
        assert harness.provider.calls == 2
        assert first_path.read_bytes() == first_bytes
        assert harness.state.voice_tracks[first_pending.id].checksum == first_checksum

        queued_before_retry = list(harness.queued)
        retry_response = client.post(f"/api/v1/voice-tracks/{first_pending.id}/retry")
        assert retry_response.status_code == 200
        VoiceTrackResponse.model_validate(retry_response.json())
        assert harness.queued == queued_before_retry
        assert len(harness.state.voice_tracks) == 2

        final_list = client.get("/api/v1/scripts/4/voice-tracks")
        assert len(final_list.json()) == 2
        assert all(item["status"] == "completed" for item in final_list.json())


def test_generation_failure_get_retry_and_same_row_recovery(tmp_path: Path, monkeypatch) -> None:
    harness = Phase5Harness(monkeypatch, tmp_path)

    with harness.client() as client:
        create_response = client.post("/api/v1/scripts/4/voice-tracks", json=request_options())
        voice_track_id = VoiceTrackStatusResponse.model_validate(create_response.json()).id
        harness.queued.clear()
        harness.provider = FailingTTSProvider()

        with pytest.raises(VoiceGenerationError):
            harness.run_task(voice_track_id)

        failed_row = harness.state.voice_tracks[voice_track_id]
        assert failed_row.status == VoiceTrackStatus.FAILED
        assert failed_row.completed_at is None
        assert failed_row.error_message == "local TTS failed"
        assert harness.state.events[-1] == "commit"

        failed_response = client.get(f"/api/v1/voice-tracks/{voice_track_id}")
        failed = VoiceTrackStatusResponse.model_validate(failed_response.json())
        assert failed_response.status_code == 200
        assert failed.status == VoiceTrackStatus.FAILED
        assert failed.error_message == "local TTS failed"

        commits_before_retry = harness.session.commits
        retry_response = client.post(f"/api/v1/voice-tracks/{voice_track_id}/retry")
        retry = VoiceTrackStatusResponse.model_validate(retry_response.json())
        assert retry_response.status_code == 202
        assert retry.id == voice_track_id
        assert retry.status == VoiceTrackStatus.PENDING
        assert retry.error_message is None
        assert harness.publication_commits[-1] == commits_before_retry + 1
        assert harness.queued == [voice_track_id]
        assert len(harness.state.voice_tracks) == 1

        harness.provider = CountingLocalTTSProvider(tmp_path)
        recovery_result = harness.run_task(harness.queued.pop())
        assert recovery_result == {
            "voice_track_id": voice_track_id,
            "voice_track_status": "completed",
            "storage_key": f"voice/{voice_track_id}/audio.wav",
        }
        assert harness.provider.calls == 1
        assert harness.artifact_path(voice_track_id).exists()
        recovered = VoiceTrackResponse.model_validate(
            client.get(f"/api/v1/voice-tracks/{voice_track_id}").json()
        )
        assert recovered.status == VoiceTrackStatus.COMPLETED
        assert len(harness.state.voice_tracks) == 1


def test_broker_failure_persists_same_row_without_artifact_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    harness = Phase5Harness(monkeypatch, tmp_path)

    def fail_publish(voice_track_id: int) -> None:
        assert harness.state.events[-1] == "commit"
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(voice_endpoint.generate_voice, "delay", fail_publish)

    with harness.client() as client:
        response = client.post("/api/v1/scripts/4/voice-tracks", json=request_options())

        assert response.status_code == 503
        assert response.json() == {"detail": "Voice generation could not be queued"}
        assert len(harness.state.voice_tracks) == 1
        failed_row = next(iter(harness.state.voice_tracks.values()))
        assert failed_row.status == VoiceTrackStatus.FAILED
        assert failed_row.storage_key is None
        assert failed_row.duration_seconds is None
        assert failed_row.file_size_bytes is None
        assert failed_row.checksum is None
        assert not harness.artifact_path(failed_row.id).exists()
        assert harness.state.events[-2:] == [
            f"save:{failed_row.id}:failed",
            "commit",
        ]


def test_unsupported_mp3_persists_deterministic_failure_without_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    harness = Phase5Harness(monkeypatch, tmp_path)

    with harness.client() as client:
        create_response = client.post(
            "/api/v1/scripts/4/voice-tracks",
            json=request_options(audio_format="mp3"),
        )
        pending = VoiceTrackStatusResponse.model_validate(create_response.json())
        assert create_response.status_code == 202

        with pytest.raises(VoiceGenerationError):
            harness.run_task(harness.queued.pop())

        failed_row = harness.state.voice_tracks[pending.id]
        assert failed_row.status == VoiceTrackStatus.FAILED
        assert "cannot encode mp3" in failed_row.error_message
        assert failed_row.completed_at is None
        assert not harness.artifact_path(pending.id).exists()
        assert harness.state.events[-1] == "commit"
        assert voice_task.generate_voice.autoretry_for != (VoiceGenerationError,)

        failed_response = client.get(f"/api/v1/voice-tracks/{pending.id}")
        failed = VoiceTrackStatusResponse.model_validate(failed_response.json())
        assert failed.status == VoiceTrackStatus.FAILED
