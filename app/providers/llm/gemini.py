from app.providers.llm.base import LLMProvider


class GeminiProvider(LLMProvider):
    """Reserved adapter for a future Gemini integration."""

    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        raise NotImplementedError("Gemini provider integration is not implemented yet")
