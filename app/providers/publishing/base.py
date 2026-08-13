from typing import Protocol

from app.schemas.publish_job import PublishingInput, PublishingResult


class PublishingProvider(Protocol):
    """Provider-neutral boundary for publishing a completed render artifact."""

    async def publish(self, publishing_input: PublishingInput) -> PublishingResult:
        """Publish validated data without ORM entities, sessions, or credentials."""
        ...
