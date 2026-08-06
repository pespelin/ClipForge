from sqlalchemy import inspect

from app.models.broll import BrollCollection
from app.models.script import Script
from app.models.video_render import (
    RenderAudioCodec,
    RenderFitMode,
    RenderOutputFormat,
    ResolutionPreset,
    VideoCodec,
    VideoRender,
    VideoRenderStatus,
)
from app.models.voice_track import VoiceTrack


def test_render_relationships_are_many_to_one_with_script_ownership() -> None:
    script_renders = inspect(Script).relationships.video_renders
    voice_renders = inspect(VoiceTrack).relationships.video_renders
    collection_renders = inspect(BrollCollection).relationships.video_renders
    render_mapper = inspect(VideoRender).relationships

    assert script_renders.uselist is True
    assert "delete-orphan" in script_renders.cascade
    assert voice_renders.uselist is True
    assert collection_renders.uselist is True
    assert render_mapper.script.uselist is False
    assert render_mapper.voice_track.uselist is False
    assert render_mapper.broll_collection.uselist is False


def test_script_supports_multiple_render_variants_and_optional_broll() -> None:
    script = Script(id=1, video_id="video-1", video_analysis_id=1, target_duration_seconds=30)
    voice = VoiceTrack(id=2, script=script)
    collection = BrollCollection(id=3, script=script)
    first = VideoRender(script=script, voice_track=voice, broll_collection=collection)
    second = VideoRender(script=script, voice_track=voice, broll_collection=None)

    assert script.video_renders == [first, second]
    assert voice.video_renders == [first, second]
    assert collection.video_renders == [first]
    assert second.broll_collection is None


def test_columns_defaults_json_constraints_and_deletion_policies() -> None:
    table = VideoRender.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    foreign_keys = {
        foreign_key.parent.name: foreign_key.ondelete for foreign_key in table.foreign_keys
    }

    assert table.c.status.default.arg is VideoRenderStatus.PENDING
    assert table.c.output_format.default.arg is RenderOutputFormat.MP4
    assert table.c.video_codec.default.arg is VideoCodec.H264
    assert table.c.audio_codec.default.arg is RenderAudioCodec.AAC
    assert table.c.resolution_preset.default.arg is ResolutionPreset.VERTICAL_1080X1920
    assert table.c.fit_mode.default.arg is RenderFitMode.COVER
    assert table.c.subtitle_style.nullable is False
    assert table.c.render_options.nullable is False
    assert table.c.timeline_data.nullable is False
    assert table.c.script_id.unique is not True
    assert foreign_keys == {
        "script_id": "CASCADE",
        "voice_track_id": "RESTRICT",
        "broll_collection_id": "RESTRICT",
    }
    assert {
        "ck_video_renders_status",
        "ck_video_renders_output_format",
        "ck_video_renders_video_codec",
        "ck_video_renders_audio_codec",
        "ck_video_renders_resolution_preset",
        "ck_video_renders_fit_mode",
        "ck_video_renders_width_positive",
        "ck_video_renders_height_positive",
        "ck_video_renders_fps_range",
        "ck_video_renders_duration_non_negative",
        "ck_video_renders_file_size_non_negative",
        "ck_video_renders_checksum_non_empty",
        "ck_video_renders_background_color_hex",
        "ck_video_renders_completed_content",
        "ck_video_renders_preset_dimensions",
    } <= constraint_names
