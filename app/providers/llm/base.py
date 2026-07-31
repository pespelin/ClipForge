from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Port for text-generation providers such as OpenAI, Gemini, or Claude."""

    @abstractmethod
    async def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Generate text from a prompt."""
