from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.script import ScriptStatus, ScriptTone

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
LanguageCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$",
        min_length=2,
        max_length=16,
    ),
]


class ScriptGenerationOptions(BaseModel):
    target_duration_seconds: float = Field(gt=0)
    tone: ScriptTone = ScriptTone.ENGAGING
    language: LanguageCode = "en"
    include_call_to_action: bool = True
    preferred_hook_candidate_index: int | None = Field(default=None, ge=0)
    preferred_clip_candidate_index: int | None = Field(default=None, ge=0)


class ScriptGenerationRequest(BaseModel):
    video_analysis_id: int = Field(gt=0)
    options: ScriptGenerationOptions


class ScriptSection(BaseModel):
    order: int = Field(ge=0)
    type: NonEmptyText
    text: NonEmptyText
    estimated_duration_seconds: float | None = Field(default=None, ge=0)
    source_start_time: float | None = Field(default=None, ge=0)
    source_end_time: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_source_time_range(self) -> Self:
        if (
            self.source_start_time is not None
            and self.source_end_time is not None
            and self.source_end_time <= self.source_start_time
        ):
            raise ValueError("source_end_time must be greater than source_start_time")
        return self


class ScriptStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_id: str
    video_analysis_id: int
    status: ScriptStatus
    completed_at: datetime | None = None
    error_message: str | None = None


class ScriptResponse(ScriptStatusResponse):
    title: str | None = None
    hook: str | None = None
    body: str | None = None
    call_to_action: str | None = None
    full_script: str | None = None
    estimated_duration_seconds: float | None = Field(default=None, ge=0)
    target_duration_seconds: float = Field(gt=0)
    tone: ScriptTone
    language: LanguageCode
    generation_options: ScriptGenerationOptions
    sections: list[ScriptSection]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_completed_content(self) -> Self:
        if self.status == ScriptStatus.COMPLETED:
            required_content = {
                "title": self.title,
                "hook": self.hook,
                "body": self.body,
                "full_script": self.full_script,
            }
            missing = [
                name for name, value in required_content.items() if not value or not value.strip()
            ]
            if missing:
                raise ValueError(
                    f"completed script requires non-empty content: {', '.join(missing)}"
                )
        return self
