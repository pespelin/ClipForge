from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.video_analysis import AnalysisStatus


class TopicResult(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    relevance: float | None = Field(default=None, ge=0, le=1)


class HookCandidate(BaseModel):
    text: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    reason: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "HookCandidate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class ClipCandidate(BaseModel):
    title: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    reason: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ClipCandidate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class VideoAnalysisResult(BaseModel):
    summary: str = Field(min_length=1)
    topics: list[TopicResult]
    keywords: list[str]
    sentiment: str | None = None
    hook_candidates: list[HookCandidate]
    clip_candidates: list[ClipCandidate]


class VideoAnalysisStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_id: str
    status: AnalysisStatus
    completed_at: datetime | None = None
    error_message: str | None = None


class VideoAnalysisResponse(VideoAnalysisStatusResponse):
    id: int
    summary: str | None = None
    topics: list[TopicResult]
    keywords: list[str]
    sentiment: str | None = None
    hook_candidates: list[HookCandidate]
    clip_candidates: list[ClipCandidate]
    created_at: datetime
    updated_at: datetime
