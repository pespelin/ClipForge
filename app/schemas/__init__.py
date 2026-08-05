"""Pydantic request and response contracts."""

from app.schemas.broll import (
    BrollAssetCandidate,
    BrollAssetResponse,
    BrollAssetStatusResponse,
    BrollCollectionResponse,
    BrollCollectionStatusResponse,
    BrollRetrievalOptions,
    BrollRetrievalRequest,
)
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
from app.schemas.voice_track import (
    TTSInput,
    TTSResult,
    VoiceGenerationOptions,
    VoiceGenerationRequest,
    VoiceSegment,
    VoiceTrackResponse,
    VoiceTrackStatusResponse,
)

__all__ = [
    "BrollAssetCandidate",
    "BrollAssetResponse",
    "BrollAssetStatusResponse",
    "BrollCollectionResponse",
    "BrollCollectionStatusResponse",
    "BrollRetrievalOptions",
    "BrollRetrievalRequest",
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
    "TTSInput",
    "TTSResult",
    "UploadResponse",
    "VideoAnalysisResult",
    "VideoAnalysisResponse",
    "VideoAnalysisStatusResponse",
    "VideoMetadata",
    "VideoStatusResponse",
    "VoiceGenerationOptions",
    "VoiceGenerationRequest",
    "VoiceSegment",
    "VoiceTrackResponse",
    "VoiceTrackStatusResponse",
]
