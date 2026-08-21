from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.publishing_upload_session_service import (
    PublishingUploadSessionService,
)


@dataclass(frozen=True, slots=True)
class PublishingExecutionLeaseGuard:
    """Application adapter for durable lease renewal at remote boundaries."""

    upload_session_service: PublishingUploadSessionService = field(repr=False)
    publish_job_id: int
    execution_owner: str = field(repr=False)
    execution_lease_seconds: int
    clock: Callable[[], datetime] = field(repr=False)
    persist_renewal: Callable[[], Awaitable[None]] = field(repr=False)

    async def renew(self) -> None:
        now = self.clock()
        await self.upload_session_service.renew_execution_lease(
            self.publish_job_id,
            owner=self.execution_owner,
            now=now,
            lease_expires_at=now + timedelta(seconds=self.execution_lease_seconds),
        )
        await self.persist_renewal()
