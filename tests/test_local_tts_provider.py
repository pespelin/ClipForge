import hashlib
import math
import struct
import wave
from pathlib import Path

import pytest

from app.models.voice_track import AudioFormat, VoiceStyle
from app.providers.tts import (
    LocalTTSProvider,
    UnsupportedAudioFormatError,
    UnsupportedTTSLanguageError,
    UnsupportedTTSProviderError,
    UnusableTTSInputError,
)
from app.schemas.script import ScriptSection
from app.schemas.voice_track import TTSInput


def synthesis_input(**values) -> TTSInput:
    defaults = {
        "voice_track_id": 11,
        "script_id": 4,
        "full_script": "Start strong and keep every scene focused.",
        "language": "en",
        "provider": "local",
        "voice": "default",
        "style": VoiceStyle.NEUTRAL,
        "audio_format": AudioFormat.WAV,
        "sample_rate_hz": 16000,
        "speaking_rate": 1.0,
        "pitch": 0.0,
        "volume_gain_db": 0.0,
        "script_sections": [],
    }
    defaults.update(values)
    return TTSInput.model_validate(defaults)


def artifact_path(root: Path, track_id: int = 11) -> Path:
    return root / f"voice/{track_id}/audio.wav"


def wav_samples(path: Path) -> tuple[wave._wave_params, tuple[int, ...]]:
    with wave.open(str(path), "rb") as wav_file:
        parameters = wav_file.getparams()
        frames = wav_file.readframes(wav_file.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return parameters, samples


async def test_output_bytes_and_metadata_are_deterministic(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)
    provider_input = synthesis_input()

    first = await provider.synthesize(provider_input)
    first_bytes = artifact_path(tmp_path).read_bytes()
    second = await provider.synthesize(provider_input)

    assert artifact_path(tmp_path).read_bytes() == first_bytes
    assert second == first


async def test_generates_valid_nonempty_mono_pcm_wav(tmp_path: Path) -> None:
    result = await LocalTTSProvider(tmp_path).synthesize(synthesis_input(sample_rate_hz=24000))
    path = artifact_path(tmp_path)
    parameters, samples = wav_samples(path)

    assert parameters.nchannels == 1
    assert parameters.sampwidth == 2
    assert parameters.framerate == 24000
    assert parameters.nframes > 0
    assert any(sample != 0 for sample in samples)
    assert result.duration_seconds == parameters.nframes / parameters.framerate
    assert result.duration_seconds > 0


async def test_checksum_and_file_size_match_artifact(tmp_path: Path) -> None:
    result = await LocalTTSProvider(tmp_path).synthesize(synthesis_input())
    audio_bytes = artifact_path(tmp_path).read_bytes()

    assert result.file_size_bytes == len(audio_bytes)
    assert result.checksum == f"sha256:{hashlib.sha256(audio_bytes).hexdigest()}"


async def test_faster_speaking_rate_shortens_audio(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)

    slow = await provider.synthesize(synthesis_input(speaking_rate=0.5))
    fast = await provider.synthesize(synthesis_input(speaking_rate=2.0))

    assert fast.duration_seconds < slow.duration_seconds


async def test_pitch_changes_waveform_without_changing_duration(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)

    low = await provider.synthesize(synthesis_input(pitch=-8))
    low_bytes = artifact_path(tmp_path).read_bytes()
    high = await provider.synthesize(synthesis_input(pitch=8))
    high_bytes = artifact_path(tmp_path).read_bytes()

    assert high.duration_seconds == low.duration_seconds
    assert high_bytes != low_bytes


async def test_volume_changes_amplitude_without_clipping(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)

    await provider.synthesize(synthesis_input(volume_gain_db=-20))
    _, quiet_samples = wav_samples(artifact_path(tmp_path))
    await provider.synthesize(synthesis_input(volume_gain_db=20))
    _, loud_samples = wav_samples(artifact_path(tmp_path))

    quiet_rms = math.sqrt(sum(sample**2 for sample in quiet_samples) / len(quiet_samples))
    loud_rms = math.sqrt(sum(sample**2 for sample in loud_samples) / len(loud_samples))
    assert loud_rms > quiet_rms
    assert max(abs(sample) for sample in loud_samples) < 32767


async def test_style_and_voice_labels_change_output_deterministically(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)

    neutral = await provider.synthesize(synthesis_input(style=VoiceStyle.NEUTRAL))
    neutral_bytes = artifact_path(tmp_path).read_bytes()
    energetic = await provider.synthesize(synthesis_input(style=VoiceStyle.ENERGETIC))
    energetic_bytes = artifact_path(tmp_path).read_bytes()
    alternate = await provider.synthesize(
        synthesis_input(style=VoiceStyle.NEUTRAL, voice="alternate-label")
    )
    alternate_bytes = artifact_path(tmp_path).read_bytes()

    assert energetic_bytes != neutral_bytes
    assert energetic.duration_seconds != neutral.duration_seconds
    assert alternate_bytes != neutral_bytes
    assert alternate.duration_seconds == neutral.duration_seconds


async def test_sections_produce_ordered_nonoverlapping_timing(tmp_path: Path) -> None:
    sections = [
        ScriptSection(order=3, type="hook", text="Start strong."),
        ScriptSection(order=8, type="body", text="Keep every scene focused."),
    ]
    result = await LocalTTSProvider(tmp_path).synthesize(synthesis_input(script_sections=sections))

    assert [segment.order for segment in result.segments] == [0, 1]
    assert [segment.section_type for segment in result.segments] == ["hook", "body"]
    assert [segment.source_script_section_order for segment in result.segments] == [3, 8]
    assert result.segments[0].audio_start_time == 0
    assert result.segments[0].audio_end_time <= result.segments[1].audio_start_time
    assert result.segments[-1].audio_end_time == pytest.approx(result.duration_seconds)
    assert all(segment.audio_end_time > segment.audio_start_time for segment in result.segments)


async def test_missing_sections_use_single_full_script_segment(tmp_path: Path) -> None:
    result = await LocalTTSProvider(tmp_path).synthesize(
        synthesis_input(full_script="  Normalize   this narration. ", script_sections=[])
    )

    assert len(result.segments) == 1
    assert result.segments[0].section_type == "full_script"
    assert result.segments[0].text == "Normalize this narration."
    assert result.segments[0].source_script_section_order is None
    assert result.segments[0].audio_end_time == pytest.approx(result.duration_seconds)


async def test_input_is_not_mutated(tmp_path: Path) -> None:
    provider_input = synthesis_input(
        full_script="  Preserve   the input. ",
        script_sections=[ScriptSection(order=0, type="hook", text="  Preserve this. ")],
    )
    original = provider_input.model_dump(mode="json")

    await LocalTTSProvider(tmp_path).synthesize(provider_input)

    assert provider_input.model_dump(mode="json") == original


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        ({"audio_format": AudioFormat.MP3}, UnsupportedAudioFormatError),
        ({"provider": "kokoro"}, UnsupportedTTSProviderError),
        ({"language": "tr"}, UnsupportedTTSLanguageError),
        ({"full_script": "... !!!"}, UnusableTTSInputError),
    ],
)
async def test_rejects_unsupported_or_unusable_input(
    tmp_path: Path, changes: dict, expected_error: type[Exception]
) -> None:
    provider = LocalTTSProvider(tmp_path)

    with pytest.raises(expected_error):
        await provider.synthesize(synthesis_input(**changes))

    assert not artifact_path(tmp_path).exists()


async def test_accepts_english_region_variant_and_unicode_deterministically(
    tmp_path: Path,
) -> None:
    provider = LocalTTSProvider(tmp_path)
    provider_input = synthesis_input(
        language="en-US",
        full_script="Café ideas stay deterministic ☕.",
    )

    first = await provider.synthesize(provider_input)
    first_bytes = artifact_path(tmp_path).read_bytes()
    second = await provider.synthesize(provider_input)

    assert second == first
    assert artifact_path(tmp_path).read_bytes() == first_bytes


async def test_storage_key_is_deterministic_and_same_track_overwrites_safely(
    tmp_path: Path,
) -> None:
    provider = LocalTTSProvider(tmp_path)
    provider_input = synthesis_input(voice_track_id=15)

    first = await provider.synthesize(provider_input)
    path = artifact_path(tmp_path, 15)
    expected_bytes = path.read_bytes()
    path.write_bytes(b"corrupt")
    second = await provider.synthesize(provider_input)

    assert first.storage_key == second.storage_key == "voice/15/audio.wav"
    assert path.read_bytes() == expected_bytes


async def test_distinct_tracks_do_not_overwrite_each_other(tmp_path: Path) -> None:
    provider = LocalTTSProvider(tmp_path)

    first = await provider.synthesize(synthesis_input(voice_track_id=1))
    first_bytes = artifact_path(tmp_path, 1).read_bytes()
    second = await provider.synthesize(synthesis_input(voice_track_id=2))

    assert first.storage_key == "voice/1/audio.wav"
    assert second.storage_key == "voice/2/audio.wav"
    assert artifact_path(tmp_path, 1).read_bytes() == first_bytes
    assert artifact_path(tmp_path, 2).exists()
