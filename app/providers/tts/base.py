from typing import Protocol

from app.schemas.voice_track import TTSInput, TTSResult


class TTSProvider(Protocol):
    """Provider-neutral boundary for structured voice synthesis."""

    async def synthesize(self, synthesis_input: TTSInput) -> TTSResult:
        """Synthesize a voice artifact without exposing vendor-specific types."""
        ...
