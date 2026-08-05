from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrackStatus
from app.schemas.script import LanguageCode, NonEmptyText, ScriptSection

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Checksum = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
StorageKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


class VoiceGenerationOptions(BaseModel):
    provider: Identifier = "local"
    voice: Identifier = "default"
    style: VoiceStyle = VoiceStyle.NEUTRAL
    language: LanguageCode = "en"
    audio_format: AudioFormat = AudioFormat.WAV
    sample_rate_hz: int = Field(default=24000, ge=8000, le=192000)
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=0.0, ge=-20.0, le=20.0)
    volume_gain_db: float = Field(default=0.0, ge=-60.0, le=20.0)


class VoiceSegment(BaseModel):
    order: int = Field(ge=0)
    section_type: Identifier
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    audio_start_time: float = Field(ge=0)
    audio_end_time: float = Field(ge=0)
    source_script_section_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_audio_time_range(self) -> Self:
        if self.audio_end_time <= self.audio_start_time:
            raise ValueError("audio_end_time must be greater than audio_start_time")
        return self


class VoiceGenerationRequest(BaseModel):
    script_id: int = Field(gt=0)
    options: VoiceGenerationOptions = Field(default_factory=VoiceGenerationOptions)


class TTSInput(BaseModel):
    voice_track_id: int = Field(gt=0)
    script_id: int = Field(gt=0)
    full_script: NonEmptyText
    language: LanguageCode
    provider: Identifier
    voice: Identifier
    style: VoiceStyle
    audio_format: AudioFormat
    sample_rate_hz: int = Field(ge=8000, le=192000)
    speaking_rate: float = Field(ge=0.5, le=2.0)
    pitch: float = Field(ge=-20.0, le=20.0)
    volume_gain_db: float = Field(ge=-60.0, le=20.0)
    script_sections: list[ScriptSection]


class TTSResult(BaseModel):
    storage_key: StorageKey
    duration_seconds: float = Field(gt=0)
    file_size_bytes: int = Field(ge=0)
    checksum: Checksum | None = None
    segments: list[VoiceSegment]


class VoiceTrackStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    script_id: int
    status: VoiceTrackStatus
    completed_at: datetime | None = None
    error_message: str | None = None


class VoiceTrackResponse(VoiceTrackStatusResponse):
    provider: Identifier
    voice: Identifier
    style: VoiceStyle
    language: LanguageCode
    audio_format: AudioFormat
    sample_rate_hz: int = Field(ge=8000, le=192000)
    speaking_rate: float = Field(ge=0.5, le=2.0)
    pitch: float = Field(ge=-20.0, le=20.0)
    volume_gain_db: float = Field(ge=-60.0, le=20.0)
    generation_options: VoiceGenerationOptions
    segments: list[VoiceSegment]
    storage_key: StorageKey | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    checksum: Checksum | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_completed_content(self) -> Self:
        if self.status == VoiceTrackStatus.COMPLETED:
            if not self.storage_key or not self.storage_key.strip():
                raise ValueError("completed voice track requires a non-empty storage_key")
            if self.duration_seconds is None or self.duration_seconds <= 0:
                raise ValueError("completed voice track requires a positive duration_seconds")
            if self.file_size_bytes is None:
                raise ValueError("completed voice track requires file_size_bytes")
        return self
