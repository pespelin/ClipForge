from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video_render import VideoRender
from app.repositories.video_render_repository import VideoRenderRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def video_render() -> VideoRender:
    return VideoRender(script_id=1, voice_track_id=2)


async def test_create_adds_and_flushes(session: AsyncMock, video_render: VideoRender) -> None:
    repository = VideoRenderRepository(session)

    result = await repository.create(video_render)

    session.add.assert_called_once_with(video_render)
    session.flush.assert_awaited_once_with()
    assert result is video_render


async def test_get_returns_render(session: AsyncMock, video_render: VideoRender) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = video_render
    session.execute.return_value = query_result

    assert await VideoRenderRepository(session).get(1) is video_render
    session.execute.assert_awaited_once()


@pytest.mark.parametrize(
    ("method", "expected_where"),
    [
        ("get_by_script_id", "video_renders.script_id"),
        ("get_by_voice_track_id", "video_renders.voice_track_id"),
    ],
)
async def test_lists_variants_newest_first(
    session: AsyncMock, video_render: VideoRender, method: str, expected_where: str
) -> None:
    scalars = Mock()
    scalars.all.return_value = [video_render]
    query_result = Mock()
    query_result.scalars.return_value = scalars
    session.execute.return_value = query_result
    repository = VideoRenderRepository(session)

    result = await getattr(repository, method)(1)

    statement = str(session.execute.await_args.args[0])
    assert expected_where in statement
    assert "ORDER BY video_renders.created_at DESC, video_renders.id DESC" in statement
    assert result == [video_render]


async def test_save_flushes_and_commit_owns_transaction(
    session: AsyncMock, video_render: VideoRender
) -> None:
    repository = VideoRenderRepository(session)

    result = await repository.save(video_render)
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    assert result is video_render
