from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice_track import VoiceTrack
from app.repositories.voice_track_repository import VoiceTrackRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def voice_track() -> VoiceTrack:
    return VoiceTrack(script_id=1)


async def test_create_adds_and_flushes_track(session: AsyncMock, voice_track: VoiceTrack) -> None:
    repository = VoiceTrackRepository(session)

    result = await repository.create(voice_track)

    session.add.assert_called_once_with(voice_track)
    session.flush.assert_awaited_once_with()
    assert result is voice_track


async def test_get_returns_track(session: AsyncMock, voice_track: VoiceTrack) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = voice_track
    session.execute.return_value = query_result
    repository = VoiceTrackRepository(session)

    result = await repository.get(1)

    session.execute.assert_awaited_once()
    assert result is voice_track


async def test_list_returns_voice_variants_newest_first(
    session: AsyncMock, voice_track: VoiceTrack
) -> None:
    scalars = Mock()
    scalars.all.return_value = [voice_track]
    query_result = Mock()
    query_result.scalars.return_value = scalars
    session.execute.return_value = query_result
    repository = VoiceTrackRepository(session)

    result = await repository.get_by_script_id(1)

    statement = session.execute.await_args.args[0]
    assert "ORDER BY voice_tracks.created_at DESC" in str(statement)
    assert result == [voice_track]


async def test_save_flushes_and_commit_owns_transaction(
    session: AsyncMock, voice_track: VoiceTrack
) -> None:
    repository = VoiceTrackRepository(session)

    result = await repository.save(voice_track)
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    assert result is voice_track
