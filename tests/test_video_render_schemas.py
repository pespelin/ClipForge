from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.video_render import (
    RenderAudioCodec,
    RenderFitMode,
    RenderOutputFormat,
    RenderTimelineItemType,
    ResolutionPreset,
    VideoCodec,
    VideoRenderStatus,
)
from app.schemas.video_render import (
    RenderOptions,
    RenderTimelineItem,
    SubtitleStyle,
    VideoRenderRequest,
    VideoRenderResponse,
    VideoRenderStatusResponse,
)


def response_data(**values) -> dict:
    timestamp = datetime.now(UTC)
    options = RenderOptions()
    defaults = {
        "id": 1,
        "script_id": 2,
        "voice_track_id": 3,
        "broll_collection_id": None,
        "status": VideoRenderStatus.PENDING,
        "output_format": options.output_format,
        "video_codec": options.video_codec,
        "audio_codec": options.audio_codec,
        "resolution_preset": options.resolution_preset,
        "width": options.width,
        "height": options.height,
        "fps": options.fps,
        "fit_mode": options.fit_mode,
        "background_color": options.background_color,
        "subtitle_enabled": options.subtitle_enabled,
        "subtitle_style": options.subtitle_style.model_dump(mode="json"),
        "render_options": options.model_dump(mode="json"),
        "timeline_data": [],
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


def test_render_options_have_shorts_defaults() -> None:
    result = RenderOptions()

    assert result.output_format is RenderOutputFormat.MP4
    assert result.video_codec is VideoCodec.H264
    assert result.audio_codec is RenderAudioCodec.AAC
    assert result.resolution_preset is ResolutionPreset.VERTICAL_1080X1920
    assert (result.width, result.height) == (1080, 1920)
    assert result.fps == 30
    assert result.fit_mode is RenderFitMode.COVER
    assert result.background_color == "#000000"
    assert result.subtitle_enabled is True
    assert result.normalize_audio is True
    assert result.target_loudness_lufs == -14
    assert result.include_broll is True
    assert result.include_original_video is False
    assert result.watermark_text is None


@pytest.mark.parametrize(
    ("preset", "width", "height"),
    [
        (ResolutionPreset.VERTICAL_1080X1920, 1080, 1920),
        (ResolutionPreset.VERTICAL_720X1280, 720, 1280),
        (ResolutionPreset.SQUARE_1080X1080, 1080, 1080),
        (ResolutionPreset.LANDSCAPE_1920X1080, 1920, 1080),
    ],
)
def test_presets_accept_only_expected_dimensions(preset, width: int, height: int) -> None:
    result = RenderOptions(resolution_preset=preset, width=width, height=height)
    assert (result.width, result.height) == (width, height)

    with pytest.raises(ValidationError):
        RenderOptions(resolution_preset=preset, width=width + 1, height=height)


def test_custom_resolution_requires_explicit_positive_dimensions() -> None:
    result = RenderOptions(resolution_preset="custom", width=900, height=1600)
    assert (result.width, result.height) == (900, 1600)

    with pytest.raises(ValidationError):
        RenderOptions(resolution_preset="custom")
    with pytest.raises(ValidationError):
        RenderOptions(resolution_preset="custom", width=0, height=1600)


@pytest.mark.parametrize("fps", [0, 121])
def test_fps_bounds(fps: float) -> None:
    with pytest.raises(ValidationError):
        RenderOptions(fps=fps)


@pytest.mark.parametrize("field", ["background_color"])
@pytest.mark.parametrize("color", ["000000", "#FFF", "#GGGGGG", "red"])
def test_render_colors_require_six_digit_hex(field: str, color: str) -> None:
    with pytest.raises(ValidationError):
        RenderOptions(**{field: color})


def test_subtitle_style_defaults_and_validation() -> None:
    result = SubtitleStyle()
    assert result.font_family == "Arial"
    assert result.position == "bottom"
    assert result.font_weight == 700

    for changes in (
        {"font_family": "   "},
        {"font_size": 7},
        {"font_weight": 750},
        {"text_color": "white"},
        {"position": "left"},
        {"max_chars_per_line": 7},
        {"stroke_width": -1},
    ):
        with pytest.raises(ValidationError):
            SubtitleStyle(**changes)


@pytest.mark.parametrize("loudness", [-30.01, -4.99])
def test_loudness_bounds(loudness: float) -> None:
    with pytest.raises(ValidationError):
        RenderOptions(target_loudness_lufs=loudness)


def test_watermark_is_trimmed_and_nonblank() -> None:
    assert RenderOptions(watermark_text="  ClipForge  ").watermark_text == "ClipForge"
    with pytest.raises(ValidationError):
        RenderOptions(watermark_text="   ")


def test_timeline_item_validates_and_normalizes_snapshot_fields() -> None:
    result = RenderTimelineItem(
        order=0,
        item_type=RenderTimelineItemType.BROLL_VIDEO,
        script_section_order=0,
        broll_asset_id=4,
        source_storage_key=" media/clip.mp4 ",
        source_start_time=1,
        source_end_time=4,
        timeline_start_time=0,
        timeline_end_time=3,
        text="Caption",
        transition="cut",
        metadata={"source": "selected"},
    )

    assert result.source_storage_key == "media/clip.mp4"
    assert result.metadata == {"source": "selected"}


@pytest.mark.parametrize(
    "changes",
    [
        {"order": -1},
        {"timeline_start_time": -1},
        {"timeline_start_time": 1, "timeline_end_time": 1},
        {"timeline_start_time": 2, "timeline_end_time": 1},
        {"source_start_time": -1, "source_end_time": 1},
        {"source_start_time": 1, "source_end_time": None},
        {"source_start_time": 2, "source_end_time": 1},
    ],
)
def test_timeline_rejects_invalid_times(changes: dict) -> None:
    values = {
        "order": 0,
        "item_type": "background",
        "timeline_start_time": 0,
        "timeline_end_time": 1,
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        RenderTimelineItem(**values)


def test_request_contains_input_references_and_default_options() -> None:
    request = VideoRenderRequest(voice_track_id=3, broll_collection_id=4)
    assert request.voice_track_id == 3
    assert request.broll_collection_id == 4
    assert request.options == RenderOptions()


def test_status_and_completed_responses_validate() -> None:
    status = VideoRenderStatusResponse.model_validate(response_data())
    assert status.status is VideoRenderStatus.PENDING

    completed = VideoRenderResponse.model_validate(
        response_data(
            status="completed",
            storage_key="renders/1/output.mp4",
            duration_seconds=15,
            file_size_bytes=4096,
            checksum="sha256:abc",
            completed_at=datetime.now(UTC),
            timeline_data=[
                {
                    "order": 0,
                    "item_type": "background",
                    "timeline_start_time": 0,
                    "timeline_end_time": 15,
                    "metadata": {},
                }
            ],
        )
    )
    assert completed.storage_key == "renders/1/output.mp4"


@pytest.mark.parametrize(
    "changes",
    [
        {"storage_key": None, "duration_seconds": 1, "file_size_bytes": 0},
        {"storage_key": "render.mp4", "duration_seconds": 0, "file_size_bytes": 0},
        {"storage_key": "render.mp4", "duration_seconds": 1, "file_size_bytes": None},
        {"storage_key": "render.mp4", "duration_seconds": 1, "file_size_bytes": -1},
    ],
)
def test_completed_render_requires_valid_artifact(changes: dict) -> None:
    with pytest.raises(ValidationError):
        VideoRenderResponse.model_validate(
            response_data(status=VideoRenderStatus.COMPLETED, **changes)
        )


def test_checksum_is_nonblank_and_timeline_order_is_deterministic() -> None:
    with pytest.raises(ValidationError):
        VideoRenderResponse.model_validate(response_data(checksum="   "))
    with pytest.raises(ValidationError):
        VideoRenderResponse.model_validate(
            response_data(
                timeline_data=[
                    {
                        "order": 1,
                        "item_type": "subtitle",
                        "timeline_start_time": 0,
                        "timeline_end_time": 1,
                    },
                    {
                        "order": 0,
                        "item_type": "narration",
                        "timeline_start_time": 0,
                        "timeline_end_time": 1,
                    },
                ]
            )
        )
