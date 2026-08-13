from datetime import date, datetime
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.publish_job import PublishPlatform, PublishStatus, PublishVisibility
from app.schemas.script import LanguageCode

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
AccountReference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
StorageKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
Checksum = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
RemoteIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
RemoteStatus = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class PublishOptions(BaseModel):
    platform: PublishPlatform = PublishPlatform.YOUTUBE
    account_reference: AccountReference
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    made_for_kids: bool = False
    notify_subscribers: bool = False
    language: LanguageCode = "en"
    scheduled_publish_at: datetime | None = None

    @field_validator("scheduled_publish_at")
    @classmethod
    def require_scheduling_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_publish_at must include timezone information")
        return value


class PublishMetadata(BaseModel):
    title: Title
    description: str | None = Field(default=None, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    category: Category | None = None
    recording_date: date | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = value.strip()
            if not tag:
                raise ValueError("tags must not contain blank values")
            if len(tag) > 100:
                raise ValueError("tags must not exceed 100 characters")
            key = tag.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(tag)
        return normalized


class PublishRequest(BaseModel):
    metadata: PublishMetadata
    options: PublishOptions


class PublishJobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_render_id: int
    status: PublishStatus
    platform: PublishPlatform
    scheduled_publish_at: datetime | None = None
    published_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    @field_validator("scheduled_publish_at", "published_at", "completed_at")
    @classmethod
    def require_response_timezones(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("publication datetimes must include timezone information")
        return value


class PublishJobResponse(PublishJobStatusResponse):
    account_reference: AccountReference
    title: Title
    description: str | None = Field(default=None, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    category: Category | None = None
    visibility: PublishVisibility
    made_for_kids: bool
    notify_subscribers: bool
    language: LanguageCode
    recording_date: date | None = None
    publish_options: PublishOptions
    source_storage_key: StorageKey
    source_checksum: Checksum | None = None
    source_file_size_bytes: int = Field(ge=0)
    source_duration_seconds: float = Field(gt=0)
    remote_media_id: RemoteIdentifier | None = None
    remote_url: HttpUrl | None = None
    remote_status: RemoteStatus | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_published_result(self) -> Self:
        if self.status == PublishStatus.PUBLISHED:
            if self.remote_media_id is None:
                raise ValueError("published job requires remote_media_id")
            if self.published_at is None:
                raise ValueError("published job requires published_at")
            if self.completed_at is None:
                raise ValueError("published job requires completed_at")
        return self
