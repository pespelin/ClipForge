from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publish_job import PublishPlatform
from app.models.publishing_upload_session import PublishingUploadSession
from app.repositories.publishing_upload_session_repository import (
    PublishingUploadSessionRepository,
)


@pytest.fixture
def db_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def upload_session() -> PublishingUploadSession:
    return PublishingUploadSession(
        publish_job_id=7,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted-session-uri",
        total_bytes=10,
        next_byte_offset=4,
    )


async def test_create_adds_and_flushes_without_transaction(
    db_session: AsyncMock, upload_session: PublishingUploadSession
) -> None:
    result = await PublishingUploadSessionRepository(db_session).create(upload_session)

    db_session.add.assert_called_once_with(upload_session)
    db_session.flush.assert_awaited_once_with()
    db_session.commit.assert_not_awaited()
    db_session.rollback.assert_not_awaited()
    assert result is upload_session


async def test_save_flushes_without_transaction(
    db_session: AsyncMock, upload_session: PublishingUploadSession
) -> None:
    result = await PublishingUploadSessionRepository(db_session).save(upload_session)

    db_session.flush.assert_awaited_once_with()
    db_session.commit.assert_not_awaited()
    db_session.rollback.assert_not_awaited()
    assert result is upload_session


@pytest.mark.parametrize(
    ("method", "argument", "expected_where"),
    [
        ("get_by_id", 3, "publishing_upload_sessions.id"),
        (
            "get_by_publish_job_id",
            7,
            "publishing_upload_sessions.publish_job_id",
        ),
    ],
)
async def test_get_queries_return_matching_session(
    db_session: AsyncMock,
    upload_session: PublishingUploadSession,
    method: str,
    argument: int,
    expected_where: str,
) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = upload_session
    db_session.execute.return_value = query_result

    result = await getattr(PublishingUploadSessionRepository(db_session), method)(argument)

    assert expected_where in str(db_session.execute.await_args.args[0])
    assert result is upload_session
    db_session.commit.assert_not_awaited()
    db_session.rollback.assert_not_awaited()


async def test_delete_deletes_and_flushes_without_transaction(
    db_session: AsyncMock, upload_session: PublishingUploadSession
) -> None:
    result = await PublishingUploadSessionRepository(db_session).delete(upload_session)

    db_session.delete.assert_awaited_once_with(upload_session)
    db_session.flush.assert_awaited_once_with()
    db_session.commit.assert_not_awaited()
    db_session.rollback.assert_not_awaited()
    assert result is None
