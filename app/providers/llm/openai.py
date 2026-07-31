from app.providers.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible LLM adapter placeholder."""

    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        raise NotImplementedError("OpenAI provider integration is not implemented yet")
