from pathlib import Path

from app.providers.tts.base import TTSProvider


class KokoroTTSProvider(TTSProvider):
    """Kokoro TTS adapter placeholder."""

    async def synthesize(self, text: str, output_path: Path, *, voice: str | None = None) -> Path:
        raise NotImplementedError("Kokoro TTS provider integration is not implemented yet")
