from pydantic import BaseModel, ConfigDict


class VideoMetadata(BaseModel):
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    bitrate: int | None = None
    audio_codec: str | None = None
    file_size: int


class UploadResponse(BaseModel):
    video_id: str
    status: str


class VideoStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    metadata: VideoMetadata | None
    transcript_ready: bool
    language: str | None = None
    duration: float | None = None
