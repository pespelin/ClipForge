from app.schemas.voice_track import TTSInput, TTSResult


class KokoroTTSProvider:
    """Kokoro TTS adapter placeholder."""

    async def synthesize(self, synthesis_input: TTSInput) -> TTSResult:
        raise NotImplementedError("Kokoro TTS provider integration is not implemented yet")
