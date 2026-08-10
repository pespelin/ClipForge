from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.broll import BrollMediaType, BrollProvider
from app.models.video_render import (
    RenderAudioCodec,
    RenderFitMode,
    RenderOutputFormat,
    RenderTimelineItemType,
    ResolutionPreset,
    VideoCodec,
    VideoRenderStatus,
)

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Checksum = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
StorageKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]

PRESET_DIMENSIONS: dict[ResolutionPreset, tuple[int, int]] = {
    ResolutionPreset.VERTICAL_1080X1920: (1080, 1920),
    ResolutionPreset.VERTICAL_720X1280: (720, 1280),
    ResolutionPreset.SQUARE_1080X1080: (1080, 1080),
    ResolutionPreset.LANDSCAPE_1920X1080: (1920, 1080),
}


class SubtitleStyle(BaseModel):
    font_family: NonEmptyText = "Arial"
    font_size: int = Field(default=48, ge=8, le=200)
    font_weight: int = Field(default=700, ge=100, le=900, multiple_of=100)
    text_color: HexColor = "#FFFFFF"
    background_color: HexColor = "#000000"
    position: Annotated[str, StringConstraints(pattern=r"^(top|center|bottom)$")] = "bottom"
    max_chars_per_line: int = Field(default=32, ge=8, le=100)
    stroke_width: float = Field(default=2.0, ge=0, le=20)
    stroke_color: HexColor = "#000000"


class RenderOptions(BaseModel):
    output_format: RenderOutputFormat = RenderOutputFormat.MP4
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: RenderAudioCodec = RenderAudioCodec.AAC
    resolution_preset: ResolutionPreset = ResolutionPreset.VERTICAL_1080X1920
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: float = Field(default=30.0, ge=1, le=120)
    fit_mode: RenderFitMode = RenderFitMode.COVER
    background_color: HexColor = "#000000"
    subtitle_enabled: bool = True
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    normalize_audio: bool = True
    target_loudness_lufs: float = Field(default=-14.0, ge=-30.0, le=-5.0)
    include_broll: bool = True
    include_original_video: bool = False
    watermark_text: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.resolution_preset == ResolutionPreset.CUSTOM:
            if not {"width", "height"} <= self.model_fields_set:
                raise ValueError("custom resolution requires explicit width and height")
            return self
        expected = PRESET_DIMENSIONS[self.resolution_preset]
        if (self.width, self.height) != expected:
            raise ValueError(f"{self.resolution_preset.value} requires dimensions {expected}")
        return self


class RenderTimelineItem(BaseModel):
    order: int = Field(ge=0)
    item_type: RenderTimelineItemType
    script_section_order: int | None = Field(default=None, ge=0)
    broll_asset_id: int | None = Field(default=None, gt=0)
    source_storage_key: StorageKey | None = None
    source_start_time: float | None = Field(default=None, ge=0)
    source_end_time: float | None = Field(default=None, ge=0)
    timeline_start_time: float = Field(ge=0)
    timeline_end_time: float = Field(ge=0)
    text: str | None = None
    transition: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_ranges(self) -> Self:
        if self.timeline_end_time <= self.timeline_start_time:
            raise ValueError("timeline_end_time must be greater than timeline_start_time")
        if (self.source_start_time is None) != (self.source_end_time is None):
            raise ValueError("source_start_time and source_end_time must be provided together")
        if (
            self.source_start_time is not None
            and self.source_end_time is not None
            and self.source_end_time <= self.source_start_time
        ):
            raise ValueError("source_end_time must be greater than source_start_time")
        return self


class VideoRenderRequest(BaseModel):
    voice_track_id: int = Field(gt=0)
    broll_collection_id: int | None = Field(default=None, gt=0)
    options: RenderOptions = Field(default_factory=RenderOptions)


class SelectedBrollAssetInput(BaseModel):
    asset_id: int = Field(gt=0)
    script_section_order: int | None = Field(default=None, ge=0)
    provider: BrollProvider
    external_id: NonEmptyText | None = None
    media_type: BrollMediaType
    storage_key: StorageKey | None = None
    source_url: str | None = None
    download_url: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata_data: dict[str, Any] = Field(default_factory=dict)


class VideoRenderInput(BaseModel):
    render_id: int = Field(gt=0)
    script_id: int = Field(gt=0)
    voice_track_id: int = Field(gt=0)
    broll_collection_id: int | None = Field(default=None, gt=0)
    render_options: RenderOptions
    script_full_text: NonEmptyText
    script_sections: list[dict[str, Any]]
    voice_storage_key: StorageKey
    voice_duration_seconds: float = Field(gt=0)
    voice_segments: list[dict[str, Any]]
    selected_broll_assets: list[SelectedBrollAssetInput]
    timeline: list[RenderTimelineItem] = Field(min_length=1)
    output_storage_key: StorageKey


class VideoRenderResult(BaseModel):
    storage_key: StorageKey
    duration_seconds: float = Field(gt=0)
    file_size_bytes: int = Field(ge=0)
    checksum: Checksum | None = None
    timeline: list[RenderTimelineItem] = Field(min_length=1)
    metadata_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline_order(self) -> Self:
        orders = [item.order for item in self.timeline]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("timeline must have unique items ordered by order")
        return self


class VideoRenderStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    script_id: int
    voice_track_id: int
    broll_collection_id: int | None = None
    status: VideoRenderStatus
    completed_at: datetime | None = None
    error_message: str | None = None


class VideoRenderResponse(VideoRenderStatusResponse):
    output_format: RenderOutputFormat
    video_codec: VideoCodec
    audio_codec: RenderAudioCodec
    resolution_preset: ResolutionPreset
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(ge=1, le=120)
    fit_mode: RenderFitMode
    background_color: HexColor
    subtitle_enabled: bool
    subtitle_style: SubtitleStyle
    render_options: RenderOptions
    timeline_data: list[RenderTimelineItem]
    storage_key: StorageKey | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    checksum: Checksum | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_completed_artifact(self) -> Self:
        orders = [item.order for item in self.timeline_data]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("timeline_data must have unique items ordered by order")
        if self.status == VideoRenderStatus.COMPLETED:
            if self.storage_key is None:
                raise ValueError("completed render requires storage_key")
            if self.duration_seconds is None or self.duration_seconds <= 0:
                raise ValueError("completed render requires positive duration_seconds")
            if self.file_size_bytes is None:
                raise ValueError("completed render requires file_size_bytes")
        return self
