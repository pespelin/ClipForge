from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video_analysis import VideoAnalysis
from app.repositories.video_analysis_repository import VideoAnalysisRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_create_adds_and_flushes_analysis(session: AsyncMock) -> None:
    repository = VideoAnalysisRepository(session)
    analysis = VideoAnalysis(video_id="video-1")

    result = await repository.create(analysis)

    session.add.assert_called_once_with(analysis)
    session.flush.assert_awaited_once_with()
    assert result is analysis


async def test_get_by_video_id_returns_analysis(session: AsyncMock) -> None:
    analysis = VideoAnalysis(video_id="video-1")
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = analysis
    session.execute.return_value = query_result
    repository = VideoAnalysisRepository(session)

    result = await repository.get_by_video_id("video-1")

    session.execute.assert_awaited_once()
    assert result is analysis


async def test_save_flushes_pending_changes(session: AsyncMock) -> None:
    repository = VideoAnalysisRepository(session)
    analysis = VideoAnalysis(video_id="video-1")

    result = await repository.save(analysis)

    session.flush.assert_awaited_once_with()
    assert result is analysis
