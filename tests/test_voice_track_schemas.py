from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrackStatus
from app.schemas.voice_track import (
    VoiceGenerationOptions,
    VoiceGenerationRequest,
    VoiceSegment,
    VoiceTrackResponse,
    VoiceTrackStatusResponse,
)


def options(**values) -> VoiceGenerationOptions:
    return VoiceGenerationOptions.model_validate(values)


def response_data(**values) -> dict:
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "script_id": 2,
        "status": VoiceTrackStatus.PENDING,
        "provider": "local",
        "voice": "default",
        "style": VoiceStyle.NEUTRAL,
        "language": "en",
        "audio_format": AudioFormat.WAV,
        "sample_rate_hz": 24000,
        "speaking_rate": 1.0,
        "pitch": 0.0,
        "volume_gain_db": 0.0,
        "generation_options": VoiceGenerationOptions().model_dump(mode="json"),
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
    return defaults


def test_generation_options_have_local_defaults() -> None:
    result = VoiceGenerationOptions()

    assert result.provider == "local"
    assert result.voice == "default"
    assert result.style is VoiceStyle.NEUTRAL
    assert result.language == "en"
    assert result.audio_format is AudioFormat.WAV
    assert result.sample_rate_hz == 24000
    assert result.speaking_rate == 1.0
    assert result.pitch == 0.0
    assert result.volume_gain_db == 0.0


def test_generation_request_defaults_options() -> None:
    request = VoiceGenerationRequest(script_id=3)

    assert request.script_id == 3
    assert request.options == VoiceGenerationOptions()


@pytest.mark.parametrize("field", ["provider", "voice"])
def test_provider_and_voice_are_trimmed_and_non_empty(field: str) -> None:
    assert getattr(options(**{field: "  custom  "}), field) == "custom"
    with pytest.raises(ValidationError):
        options(**{field: "   "})


@pytest.mark.parametrize("language", ["EN", "e", "english", "en-us", "en_US"])
def test_language_uses_existing_language_code_convention(language: str) -> None:
    with pytest.raises(ValidationError):
        options(language=language)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("speaking_rate", 0.49),
        ("speaking_rate", 2.01),
        ("pitch", -20.01),
        ("pitch", 20.01),
        ("volume_gain_db", -60.01),
        ("volume_gain_db", 20.01),
        ("sample_rate_hz", 7999),
        ("sample_rate_hz", 192001),
    ],
)
def test_generation_option_ranges(field: str, invalid_value: float) -> None:
    with pytest.raises(ValidationError):
        options(**{field: invalid_value})


def test_generation_option_range_boundaries_are_valid() -> None:
    result = options(
        speaking_rate=0.5,
        pitch=20,
        volume_gain_db=-60,
        sample_rate_hz=192000,
    )

    assert result.speaking_rate == 0.5
    assert result.pitch == 20
    assert result.volume_gain_db == -60
    assert result.sample_rate_hz == 192000


def test_segment_normalizes_text_and_validates_timestamps() -> None:
    segment = VoiceSegment(
        order=0,
        section_type=" hook ",
        text=" Opening line ",
        audio_start_time=0,
        audio_end_time=2.5,
        source_script_section_order=0,
    )

    assert segment.section_type == "hook"
    assert segment.text == "Opening line"


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 2), (0, -1), (1, 1), (2, 1)],
)
def test_segment_rejects_invalid_timestamp_range(start: float, end: float) -> None:
    with pytest.raises(ValidationError):
        VoiceSegment(
            order=0,
            section_type="body",
            text="Narration",
            audio_start_time=start,
            audio_end_time=end,
        )


def test_status_response_validates_from_attributes() -> None:
    result = VoiceTrackStatusResponse.model_validate(response_data())

    assert result.status is VoiceTrackStatus.PENDING
    assert result.script_id == 2


def test_completed_response_accepts_valid_track_and_segments() -> None:
    result = VoiceTrackResponse.model_validate(
        response_data(
            status=VoiceTrackStatus.COMPLETED,
            storage_key="voices/1/narration.wav",
            duration_seconds=12.5,
            file_size_bytes=2048,
            checksum="sha256:abc123",
            completed_at=datetime.now(UTC),
            segments=[
                {
                    "order": 0,
                    "section_type": "hook",
                    "text": "Opening line",
                    "audio_start_time": 0,
                    "audio_end_time": 2.5,
                    "source_script_section_order": 0,
                }
            ],
        )
    )

    assert result.storage_key == "voices/1/narration.wav"
    assert result.segments[0].audio_end_time == 2.5


@pytest.mark.parametrize(
    "updates",
    [
        {"storage_key": None, "duration_seconds": 1, "file_size_bytes": 0},
        {"storage_key": "   ", "duration_seconds": 1, "file_size_bytes": 0},
        {"storage_key": "voice.wav", "duration_seconds": 0, "file_size_bytes": 0},
        {"storage_key": "voice.wav", "duration_seconds": 1, "file_size_bytes": None},
        {"storage_key": "voice.wav", "duration_seconds": 1, "file_size_bytes": -1},
    ],
)
def test_completed_response_requires_artifact_content(updates: dict) -> None:
    with pytest.raises(ValidationError):
        VoiceTrackResponse.model_validate(
            response_data(status=VoiceTrackStatus.COMPLETED, **updates)
        )


def test_checksum_must_be_non_empty_when_present() -> None:
    with pytest.raises(ValidationError):
        VoiceTrackResponse.model_validate(response_data(checksum="   "))
