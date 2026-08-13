from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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


async def test_save_flushes_and_commit_owns_transaction(
    session: AsyncMock, publish_job: PublishJob
) -> None:
    repository = PublishJobRepository(session)

    result = await repository.save(publish_job)
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    assert result is publish_job
