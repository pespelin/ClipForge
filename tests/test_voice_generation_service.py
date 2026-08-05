from typing import Any

import pytest

from app.core.exceptions import (
    ScriptNotFoundError,
    ScriptNotReadyError,
    UnusableScriptContentError,
    VoiceGenerationError,
    VoiceTrackNotFoundError,
)
from app.models.script import Script, ScriptStatus
from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrack, VoiceTrackStatus
from app.schemas.voice_track import (
    TTSInput,
    TTSResult,
    VoiceGenerationOptions,
    VoiceSegment,
)
from app.services.voice_generation_service import VoiceGenerationService


class FakeScriptRepository:
    def __init__(self, script: Script | None) -> None:
        self.script = script

    async def get(self, script_id: int) -> Script | None:
        if self.script is not None and self.script.id == script_id:
            return self.script
        return None


class FakeVoiceTrackRepository:
    def __init__(self) -> None:
        self.rows: list[VoiceTrack] = []
        self.saved_statuses: list[VoiceTrackStatus] = []

    async def create(self, voice_track: VoiceTrack) -> VoiceTrack:
        voice_track.id = len(self.rows) + 1
        self.rows.append(voice_track)
        return voice_track

    async def get(self, voice_track_id: int) -> VoiceTrack | None:
        return next((row for row in self.rows if row.id == voice_track_id), None)

    async def get_by_script_id(self, script_id: int) -> list[VoiceTrack]:
        return [row for row in self.rows if row.script_id == script_id]

    async def save(self, voice_track: VoiceTrack) -> VoiceTrack:
        self.saved_statuses.append(voice_track.status)
        return voice_track


class FakeTTSProvider:
    def __init__(self, result: TTSResult | dict[str, Any]) -> None:
        self.result = result
        self.calls = 0
        self.synthesis_input: TTSInput | None = None

    async def synthesize(self, synthesis_input: TTSInput):
        self.calls += 1
        self.synthesis_input = synthesis_input
        return self.result


class FailingTTSProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, synthesis_input: TTSInput):
        self.calls += 1
        raise RuntimeError("synthesizer unavailable")


def completed_script(
    *, status: ScriptStatus = ScriptStatus.COMPLETED, full_script: str | None = None
) -> Script:
    return Script(
        id=4,
        video_id="video-1",
        video_analysis_id=7,
        status=status,
        title="Editing lessons",
        hook="Start with your strongest visual.",
        body="Remove every unnecessary pause.",
        full_script=(
            "Start with your strongest visual. Remove every unnecessary pause."
            if full_script is None
            else full_script
        ),
        estimated_duration_seconds=10,
        target_duration_seconds=30,
        tone="educational",
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
    )


def voice_options(**values) -> VoiceGenerationOptions:
    defaults = {
        "provider": "local",
        "voice": "narrator",
        "style": VoiceStyle.CONVERSATIONAL,
        "language": "en",
        "audio_format": AudioFormat.WAV,
        "sample_rate_hz": 24000,
        "speaking_rate": 1.1,
        "pitch": 1.5,
        "volume_gain_db": -2.0,
    }
    defaults.update(values)
    return VoiceGenerationOptions.model_validate(defaults)


def tts_result() -> TTSResult:
    return TTSResult(
        storage_key="voices/1/narration.wav",
        duration_seconds=9.5,
        file_size_bytes=45678,
        checksum="sha256:abc123",
        segments=[
            VoiceSegment(
                order=0,
                section_type="hook",
                text="Start with your strongest visual.",
                audio_start_time=0,
                audio_end_time=3,
                source_script_section_order=0,
            )
        ],
    )


def make_service(
    *,
    script: Script | None = None,
    tracks: FakeVoiceTrackRepository | None = None,
    provider=None,
) -> tuple[VoiceGenerationService, FakeVoiceTrackRepository]:
    repository = tracks or FakeVoiceTrackRepository()
    service = VoiceGenerationService(
        FakeScriptRepository(script),
        repository,
        provider or FakeTTSProvider(tts_result()),
    )
    return service, repository


async def test_create_voice_track_persists_pending_variant_and_options_snapshot() -> None:
    service, repository = make_service(script=completed_script())
    options = voice_options(provider=" local ", voice=" narrator ")

    track = await service.create_voice_track(4, options)

    assert track.status == VoiceTrackStatus.PENDING
    assert track.script_id == 4
    assert track.provider == "local"
    assert track.voice == "narrator"
    assert track.style is VoiceStyle.CONVERSATIONAL
    assert track.language == "en"
    assert track.audio_format is AudioFormat.WAV
    assert track.sample_rate_hz == 24000
    assert track.speaking_rate == 1.1
    assert track.pitch == 1.5
    assert track.volume_gain_db == -2.0
    assert track.generation_options == options.model_dump(mode="json")
    assert repository.rows == [track]

    options.voice = "changed-after-create"
    assert track.generation_options["voice"] == "narrator"


async def test_create_voice_track_allows_multiple_variants() -> None:
    service, repository = make_service(script=completed_script())

    first = await service.create_voice_track(4, voice_options(voice="voice-a"))
    second = await service.create_voice_track(
        4,
        voice_options(
            voice="voice-b",
            style=VoiceStyle.ENERGETIC,
            audio_format=AudioFormat.MP3,
        ),
    )

    assert first.id != second.id
    assert first.script_id == second.script_id == 4
    assert first.voice == "voice-a"
    assert second.voice == "voice-b"
    assert repository.rows == [first, second]


async def test_create_voice_track_rejects_missing_script() -> None:
    service, repository = make_service()

    with pytest.raises(ScriptNotFoundError):
        await service.create_voice_track(4, voice_options())

    assert repository.rows == []


@pytest.mark.parametrize(
    "status", [ScriptStatus.PENDING, ScriptStatus.GENERATING, ScriptStatus.FAILED]
)
async def test_create_voice_track_requires_completed_script(status: ScriptStatus) -> None:
    service, repository = make_service(script=completed_script(status=status))

    with pytest.raises(ScriptNotReadyError):
        await service.create_voice_track(4, voice_options())

    assert repository.rows == []


async def test_create_voice_track_rejects_whitespace_script() -> None:
    service, repository = make_service(script=completed_script(full_script="   "))

    with pytest.raises(UnusableScriptContentError):
        await service.create_voice_track(4, voice_options())

    assert repository.rows == []


async def test_process_voice_track_maps_result_and_provider_input() -> None:
    provider = FakeTTSProvider(tts_result().model_dump())
    script = completed_script()
    service, repository = make_service(script=script, provider=provider)
    track = await service.create_voice_track(4, voice_options())

    result = await service.process_voice_track(track.id)

    assert repository.saved_statuses == [
        VoiceTrackStatus.GENERATING,
        VoiceTrackStatus.COMPLETED,
    ]
    assert result.status == VoiceTrackStatus.COMPLETED
    assert result.storage_key == "voices/1/narration.wav"
    assert result.duration_seconds == 9.5
    assert result.file_size_bytes == 45678
    assert result.checksum == "sha256:abc123"
    assert result.segments[0]["section_type"] == "hook"
    assert result.completed_at is not None
    assert result.error_message is None

    synthesis_input = provider.synthesis_input
    assert isinstance(synthesis_input, TTSInput)
    assert synthesis_input.voice_track_id == track.id
    assert synthesis_input.script_id == script.id
    assert synthesis_input.full_script == script.full_script
    assert synthesis_input.provider == "local"
    assert synthesis_input.voice == "narrator"
    assert synthesis_input.style is VoiceStyle.CONVERSATIONAL
    assert synthesis_input.audio_format is AudioFormat.WAV
    assert synthesis_input.sample_rate_hz == 24000
    assert synthesis_input.speaking_rate == 1.1
    assert synthesis_input.pitch == 1.5
    assert synthesis_input.volume_gain_db == -2.0
    assert [section.type for section in synthesis_input.script_sections] == ["hook", "body"]
    assert not isinstance(synthesis_input, (Script, VoiceTrack))
    assert all(not isinstance(section, Script) for section in synthesis_input.script_sections)


async def test_completed_voice_track_is_idempotent() -> None:
    provider = FakeTTSProvider(tts_result())
    repository = FakeVoiceTrackRepository()
    existing = VoiceTrack(
        id=1,
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
    )
    repository.rows.append(existing)
    service, _ = make_service(script=None, tracks=repository, provider=provider)

    result = await service.process_voice_track(1)

    assert result is existing
    assert provider.calls == 0
    assert repository.saved_statuses == []


async def test_provider_failure_persists_failed_state_and_app_error() -> None:
    provider = FailingTTSProvider()
    service, repository = make_service(script=completed_script(), provider=provider)
    track = await service.create_voice_track(4, voice_options())

    with pytest.raises(VoiceGenerationError) as error_info:
        await service.process_voice_track(track.id)

    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert track.status == VoiceTrackStatus.FAILED
    assert track.completed_at is None
    assert track.error_message == "synthesizer unavailable"
    assert repository.saved_statuses == [
        VoiceTrackStatus.GENERATING,
        VoiceTrackStatus.FAILED,
    ]


async def test_invalid_provider_result_persists_useful_failure() -> None:
    provider = FakeTTSProvider(
        {
            "storage_key": "",
            "duration_seconds": -1,
            "file_size_bytes": -1,
            "segments": [],
        }
    )
    service, repository = make_service(script=completed_script(), provider=provider)
    track = await service.create_voice_track(4, voice_options())

    with pytest.raises(VoiceGenerationError) as error_info:
        await service.process_voice_track(track.id)

    assert error_info.value.__cause__ is not None
    assert track.status == VoiceTrackStatus.FAILED
    assert track.error_message == "TTS provider returned an invalid structured result"
    assert repository.saved_statuses[-1] == VoiceTrackStatus.FAILED


async def test_get_voice_track_returns_existing_and_raises_for_missing() -> None:
    service, repository = make_service(script=completed_script())
    track = await service.create_voice_track(4, voice_options())

    assert await service.get_voice_track(track.id) is track
    with pytest.raises(VoiceTrackNotFoundError):
        await service.get_voice_track(999)


async def test_list_voice_tracks_for_script_returns_variants() -> None:
    service, _ = make_service(script=completed_script())
    first = await service.create_voice_track(4, voice_options(voice="one"))
    second = await service.create_voice_track(4, voice_options(voice="two"))

    assert await service.list_voice_tracks_for_script(4) == [first, second]


async def test_list_voice_tracks_requires_existing_script() -> None:
    service, _ = make_service()

    with pytest.raises(ScriptNotFoundError):
        await service.list_voice_tracks_for_script(4)
