"""Pydantic request and response contracts."""

from app.schemas.video import UploadResponse, VideoMetadata, VideoStatusResponse
from app.schemas.video_analysis import (
    ClipCandidate,
    HookCandidate,
    TopicResult,
    VideoAnalysisResponse,
    VideoAnalysisResult,
    VideoAnalysisStatusResponse,
)

__all__ = [
    "ClipCandidate",
    "HookCandidate",
    "TopicResult",
    "UploadResponse",
    "VideoAnalysisResult",
    "VideoAnalysisResponse",
    "VideoAnalysisStatusResponse",
    "VideoMetadata",
    "VideoStatusResponse",
]
