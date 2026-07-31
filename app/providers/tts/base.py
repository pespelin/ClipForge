from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    """Port for text-to-speech engines."""

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path, *, voice: str | None = None) -> Path:
        """Synthesize speech and return the generated audio path."""
