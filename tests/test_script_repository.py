from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import Script
from app.repositories.script_repository import ScriptRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def script() -> Script:
    return Script(
        video_id="video-1",
        video_analysis_id=1,
        target_duration_seconds=45,
        language="en",
    )


async def test_create_adds_and_flushes_script(session: AsyncMock, script: Script) -> None:
    repository = ScriptRepository(session)

    result = await repository.create(script)

    session.add.assert_called_once_with(script)
    session.flush.assert_awaited_once_with()
    assert result is script


async def test_get_returns_script(session: AsyncMock, script: Script) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = script
    session.execute.return_value = query_result
    repository = ScriptRepository(session)

    result = await repository.get(1)

    session.execute.assert_awaited_once()
    assert result is script


@pytest.mark.parametrize(
    ("method_name", "identifier"),
    [("get_by_video_id", "video-1"), ("get_by_analysis_id", 1)],
)
async def test_list_queries_return_script_variants(
    session: AsyncMock, script: Script, method_name: str, identifier: str | int
) -> None:
    scalars = Mock()
    scalars.all.return_value = [script]
    query_result = Mock()
    query_result.scalars.return_value = scalars
    session.execute.return_value = query_result
    repository = ScriptRepository(session)

    result = await getattr(repository, method_name)(identifier)

    session.execute.assert_awaited_once()
    assert result == [script]


async def test_save_flushes_and_commit_owns_transaction(session: AsyncMock, script: Script) -> None:
    repository = ScriptRepository(session)

    result = await repository.save(script)
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    assert result is script
