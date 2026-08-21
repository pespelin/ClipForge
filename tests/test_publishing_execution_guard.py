from datetime import UTC, datetime, timedelta

from app.services.publishing_execution_guard import PublishingExecutionLeaseGuard

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeUploadSessionService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.call = None

    async def renew_execution_lease(self, publish_job_id: int, **values):
        self.events.append("renew")
        self.call = (publish_job_id, values)


async def test_guard_renews_with_injected_clock_then_persists() -> None:
    events: list[str] = []
    service = FakeUploadSessionService(events)

    async def persist() -> None:
        events.append("persist")

    guard = PublishingExecutionLeaseGuard(
        upload_session_service=service,
        publish_job_id=7,
        execution_owner="task-owner-secret-16e",
        execution_lease_seconds=900,
        clock=lambda: NOW,
        persist_renewal=persist,
    )

    await guard.renew()

    assert events == ["renew", "persist"]
    assert service.call == (
        7,
        {
            "owner": "task-owner-secret-16e",
            "now": NOW,
            "lease_expires_at": NOW + timedelta(seconds=900),
        },
    )
    assert "task-owner-secret-16e" not in repr(guard)
