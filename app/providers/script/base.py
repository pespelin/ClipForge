from typing import Protocol

from app.schemas.script import ScriptGenerationResult, ScriptGeneratorInput


class ScriptGenerator(Protocol):
    """Provider-neutral boundary for structured Shorts script generation."""

    async def generate(self, generation_input: ScriptGeneratorInput) -> ScriptGenerationResult:
        """Generate a structured script without exposing vendor-specific types."""
        ...
