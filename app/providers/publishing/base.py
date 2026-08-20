from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.schemas.publish_job import PublishingInput, PublishingResult


class PublishingProvider(Protocol):
    """Provider-neutral boundary for publishing a completed render artifact."""

    async def publish(self, publishing_input: PublishingInput) -> PublishingResult:
        """Publish validated data without ORM entities, sessions, or credentials."""
        ...


@dataclass(frozen=True, slots=True)
class ResumablePublishingSession:
    """Provider-neutral state required to continue a remote upload session."""

    session_uri: str = field(repr=False)
    total_bytes: int
    next_byte_offset: int = 0


@runtime_checkable
class ResumablePublishingProvider(Protocol):
    """Optional publishing capability for checkpointed resumable uploads."""

    async def initiate_upload(
        self, publishing_input: PublishingInput
    ) -> ResumablePublishingSession: ...

    async def resume_upload(
        self,
        publishing_input: PublishingInput,
        session: ResumablePublishingSession,
    ) -> PublishingResult: ...
