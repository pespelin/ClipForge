from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from app.models.broll import (
    BrollAssetStatus,
    BrollCollectionStatus,
    BrollMediaType,
    BrollOrientation,
    BrollProvider,
)
from app.schemas.script import LanguageCode

NonEmptyIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StorageKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


class BrollRetrievalOptions(BaseModel):
    provider: BrollProvider = BrollProvider.LOCAL
    media_type: BrollMediaType = BrollMediaType.VIDEO
    orientation: BrollOrientation = BrollOrientation.PORTRAIT
    max_assets_per_section: int = Field(default=3, ge=1, le=20)
    min_duration_seconds: float = Field(default=0, ge=0)
    max_duration_seconds: float = Field(default=60, ge=0)
    min_width: int | None = Field(default=720, gt=0)
    min_height: int | None = Field(default=1280, gt=0)
    safe_search: bool = True
    download_assets: bool = False
    language: LanguageCode = "en"

    @model_validator(mode="after")
    def validate_duration_range(self) -> Self:
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ValueError("max_duration_seconds must be greater than or equal to minimum")
        return self


class BrollAssetCandidate(BaseModel):
    script_section_order: int | None = Field(default=None, ge=0)
    provider: BrollProvider
    external_id: NonEmptyIdentifier | None = None
    media_type: BrollMediaType
    query: NonEmptyText
    title: str | None = None
    description: str | None = None
    source_url: HttpUrl | None = None
    preview_url: HttpUrl | None = None
    download_url: HttpUrl | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    mime_type: NonEmptyIdentifier | None = None
    attribution: str | None = None
    license_name: str | None = None
    photographer_or_creator: str | None = None
    orientation: BrollOrientation = BrollOrientation.UNKNOWN
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    metadata_data: dict[str, Any] = Field(default_factory=dict)


class MediaSearchInput(BaseModel):
    collection_id: int = Field(gt=0)
    script_id: int = Field(gt=0)
    section_order: int | None = Field(default=None, ge=0)
    section_type: NonEmptyIdentifier
    section_text: NonEmptyText
    query: NonEmptyText
    language: LanguageCode
    media_type: BrollMediaType
    orientation: BrollOrientation
    min_duration_seconds: float = Field(ge=0)
    max_duration_seconds: float = Field(ge=0)
    min_width: int | None = Field(default=None, gt=0)
    min_height: int | None = Field(default=None, gt=0)
    safe_search: bool
    max_results: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def validate_duration_range(self) -> Self:
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ValueError("max_duration_seconds must be greater than or equal to minimum")
        return self


class MediaCandidateResult(BaseModel):
    provider: BrollProvider
    external_id: NonEmptyIdentifier | None = None
    media_type: BrollMediaType
    title: str | None = None
    description: str | None = None
    source_url: HttpUrl | None = None
    preview_url: HttpUrl | None = None
    download_url: HttpUrl | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    mime_type: NonEmptyIdentifier | None = None
    attribution: str | None = None
    license_name: str | None = None
    photographer_or_creator: str | None = None
    orientation: BrollOrientation = BrollOrientation.UNKNOWN
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    metadata_data: dict[str, Any] = Field(default_factory=dict)


class BrollAssetStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collection_id: int
    status: BrollAssetStatus
    downloaded_at: datetime | None = None
    error_message: str | None = None


class BrollAssetResponse(BrollAssetStatusResponse):
    script_section_order: int | None = Field(default=None, ge=0)
    provider: BrollProvider
    external_id: NonEmptyIdentifier | None = None
    media_type: BrollMediaType
    query: NonEmptyText
    title: str | None = None
    description: str | None = None
    source_url: HttpUrl | None = None
    preview_url: HttpUrl | None = None
    download_url: HttpUrl | None = None
    storage_key: StorageKey | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    mime_type: NonEmptyIdentifier | None = None
    checksum: NonEmptyIdentifier | None = None
    attribution: str | None = None
    license_name: str | None = None
    photographer_or_creator: str | None = None
    orientation: BrollOrientation
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    metadata_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_downloaded_storage(self) -> Self:
        if self.status == BrollAssetStatus.DOWNLOADED and self.storage_key is None:
            raise ValueError("downloaded B-roll asset requires a non-empty storage_key")
        return self


class BrollCollectionStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    script_id: int
    status: BrollCollectionStatus
    completed_at: datetime | None = None
    error_message: str | None = None


class BrollCollectionResponse(BrollCollectionStatusResponse):
    provider: BrollProvider
    query_strategy: NonEmptyIdentifier
    retrieval_options: BrollRetrievalOptions
    assets: list[BrollAssetResponse]
    created_at: datetime
    updated_at: datetime


class BrollRetrievalRequest(BaseModel):
    script_id: int = Field(gt=0)
    query_strategy: NonEmptyIdentifier = "section_keywords"
    options: BrollRetrievalOptions = Field(default_factory=BrollRetrievalOptions)
