"""Pydantic request and response contracts."""

from app.schemas.script import (
    ScriptGenerationOptions,
    ScriptGenerationRequest,
    ScriptGenerationResult,
    ScriptGeneratorInput,
    ScriptResponse,
    ScriptSection,
    ScriptStatusResponse,
)
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
    "ScriptGenerationOptions",
    "ScriptGenerationResult",
    "ScriptGenerationRequest",
    "ScriptGeneratorInput",
    "ScriptResponse",
    "ScriptSection",
    "ScriptStatusResponse",
    "TopicResult",
    "UploadResponse",
    "VideoAnalysisResult",
    "VideoAnalysisResponse",
    "VideoAnalysisStatusResponse",
    "VideoMetadata",
    "VideoStatusResponse",
]
