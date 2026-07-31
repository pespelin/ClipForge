from app.providers.llm.base import LLMProvider


class FutureClaudeProvider(LLMProvider):
    """Reserved adapter for a future Claude integration."""

    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        raise NotImplementedError("Claude provider integration is not implemented yet")
