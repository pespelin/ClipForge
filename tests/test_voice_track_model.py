from sqlalchemy import inspect

from app.models.script import Script
from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrack, VoiceTrackStatus


def test_script_voice_track_relationship_is_one_to_many() -> None:
    script_tracks = inspect(Script).relationships.voice_tracks
    track_script = inspect(VoiceTrack).relationships.script

    assert script_tracks.uselist is True
    assert script_tracks.back_populates == "script"
    assert "delete-orphan" in script_tracks.cascade
    assert track_script.uselist is False
    assert track_script.back_populates == "voice_tracks"


def test_script_supports_multiple_voice_variants() -> None:
    script = Script(
        id=1,
        video_id="video-1",
        video_analysis_id=1,
        target_duration_seconds=30,
        language="en",
    )
    first = VoiceTrack(script=script, voice="voice-a")
    second = VoiceTrack(script=script, voice="voice-b")

    assert script.voice_tracks == [first, second]
    assert first.script is script
    assert second.script is script


def test_voice_track_columns_define_defaults_json_and_constraints() -> None:
    table = VoiceTrack.__table__
    constraint_names = {constraint.name for constraint in table.constraints}

    assert table.c.status.default.arg is VoiceTrackStatus.PENDING
    assert table.c.provider.default.arg == "local"
    assert table.c.voice.default.arg == "default"
    assert table.c.style.default.arg is VoiceStyle.NEUTRAL
    assert table.c.audio_format.default.arg is AudioFormat.WAV
    assert table.c.sample_rate_hz.default.arg == 24000
    assert table.c.speaking_rate.default.arg == 1.0
    assert table.c.generation_options.nullable is False
    assert table.c.segments.nullable is False
    assert table.c.script_id.unique is not True
    assert {
        "ck_voice_tracks_status",
        "ck_voice_tracks_style",
        "ck_voice_tracks_audio_format",
        "ck_voice_tracks_provider_non_empty",
        "ck_voice_tracks_voice_non_empty",
        "ck_voice_tracks_sample_rate_range",
        "ck_voice_tracks_speaking_rate_range",
        "ck_voice_tracks_pitch_range",
        "ck_voice_tracks_volume_gain_range",
        "ck_voice_tracks_duration_non_negative",
        "ck_voice_tracks_file_size_non_negative",
        "ck_voice_tracks_checksum_non_empty",
        "ck_voice_tracks_completed_content",
    } <= constraint_names
