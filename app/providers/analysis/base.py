from typing import Protocol

from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import VideoAnalysisResult


class VideoAnalyzer(Protocol):
    """Provider-independent boundary for structured transcript analysis."""

    async def analyze(
        self, transcript: str, *, metadata: VideoMetadata | None
    ) -> VideoAnalysisResult:
        """Analyze transcript text and return structured, provider-neutral results."""
        ...
