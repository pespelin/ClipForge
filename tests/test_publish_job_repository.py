from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PublishingExecutionLockUnavailableError
from app.models.publish_job import PublishJob
from app.repositories.publish_job_repository import PublishJobRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def publish_job() -> PublishJob:
    return PublishJob(video_render_id=1, account_reference="channel", title="Short")


async def test_create_adds_and_flushes(session: AsyncMock, publish_job: PublishJob) -> None:
    repository = PublishJobRepository(session)

    result = await repository.create(publish_job)

    session.add.assert_called_once_with(publish_job)
    session.flush.assert_awaited_once_with()
    assert result is publish_job


async def test_get_returns_job(session: AsyncMock, publish_job: PublishJob) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = publish_job
    session.execute.return_value = query_result

    assert await PublishJobRepository(session).get(1) is publish_job
    session.execute.assert_awaited_once()
    assert "lock_timeout" not in str(session.execute.await_args.args[0])


async def test_get_for_update_locks_matching_job_without_transaction_ownership(
    session: AsyncMock, publish_job: PublishJob
) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = publish_job
    session.execute.side_effect = [None, query_result]

    result = await PublishJobRepository(session).get_for_update(1)

    timeout_statement = str(session.execute.await_args_list[0].args[0])
    statement = str(session.execute.await_args_list[1].args[0])
    assert timeout_statement == "SET LOCAL lock_timeout = '5s'"
    assert "publish_jobs.id" in statement
    assert "FOR UPDATE" in statement
    assert result is publish_job
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


class FakePostgresError(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


async def test_get_for_update_normalizes_postgres_lock_timeout_without_db_details(
    session: AsyncMock,
) -> None:
    raw_secret = "POSTGRES_LOCK_SECRET_16B"
    db_error = OperationalError(
        "SELECT secret SQL",
        {},
        FakePostgresError(raw_secret, "55P03"),
    )
    session.execute.side_effect = [None, db_error]

    with pytest.raises(PublishingExecutionLockUnavailableError) as captured:
        await PublishJobRepository(session).get_for_update(1)

    assert raw_secret not in str(captured.value)
    assert raw_secret not in repr(captured.value)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


async def test_get_for_update_does_not_reclassify_unrelated_database_errors(
    session: AsyncMock,
) -> None:
    db_error = OperationalError(
        "SELECT 1",
        {},
        FakePostgresError("database unavailable", "08006"),
    )
    session.execute.side_effect = [None, db_error]

    with pytest.raises(OperationalError) as captured:
        await PublishJobRepository(session).get_for_update(1)

    assert captured.value is db_error


async def test_list_by_render_is_newest_first(session: AsyncMock, publish_job: PublishJob) -> None:
    scalars = Mock()
    scalars.all.return_value = [publish_job]
    query_result = Mock()
    query_result.scalars.return_value = scalars
    session.execute.return_value = query_result

    result = await PublishJobRepository(session).get_by_video_render_id(1)

    statement = str(session.execute.await_args.args[0])
    assert "publish_jobs.video_render_id" in statement
    assert "ORDER BY publish_jobs.created_at DESC, publish_jobs.id DESC" in statement
    assert result == [publish_job]


async def test_save_flushes_without_committing(session: AsyncMock, publish_job: PublishJob) -> None:
    repository = PublishJobRepository(session)

    result = await repository.save(publish_job)

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    assert result is publish_job
