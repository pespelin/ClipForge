from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broll import BrollAsset, BrollCollection
from app.repositories.broll_repository import (
    BrollAssetRepository,
    BrollCollectionRepository,
)


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def collection() -> BrollCollection:
    return BrollCollection(script_id=1)


@pytest.fixture
def asset() -> BrollAsset:
    return BrollAsset(collection_id=1, script_section_order=0, query="city skyline")


async def test_collection_create_adds_and_flushes(
    session: AsyncMock, collection: BrollCollection
) -> None:
    repository = BrollCollectionRepository(session)

    result = await repository.create(collection)

    session.add.assert_called_once_with(collection)
    session.flush.assert_awaited_once_with()
    assert result is collection


async def test_collection_get_and_list_newest_first(
    session: AsyncMock, collection: BrollCollection
) -> None:
    scalar_result = Mock()
    scalar_result.scalar_one_or_none.return_value = collection
    list_scalars = Mock()
    list_scalars.all.return_value = [collection]
    list_result = Mock()
    list_result.scalars.return_value = list_scalars
    session.execute.side_effect = [scalar_result, list_result]
    repository = BrollCollectionRepository(session)

    assert await repository.get(1) is collection
    assert await repository.get_by_script_id(1) == [collection]

    statement = session.execute.await_args_list[1].args[0]
    assert "ORDER BY broll_collections.created_at DESC, broll_collections.id DESC" in str(statement)


async def test_collection_save_and_commit(session: AsyncMock, collection: BrollCollection) -> None:
    repository = BrollCollectionRepository(session)

    assert await repository.save(collection) is collection
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()


async def test_asset_create_adds_and_flushes(session: AsyncMock, asset: BrollAsset) -> None:
    repository = BrollAssetRepository(session)

    result = await repository.create(asset)

    session.add.assert_called_once_with(asset)
    session.flush.assert_awaited_once_with()
    assert result is asset


async def test_asset_get_and_lists_use_deterministic_order(
    session: AsyncMock, asset: BrollAsset
) -> None:
    scalar_result = Mock()
    scalar_result.scalar_one_or_none.return_value = asset
    list_scalars = Mock()
    list_scalars.all.return_value = [asset]
    list_result = Mock()
    list_result.scalars.return_value = list_scalars
    section_scalars = Mock()
    section_scalars.all.return_value = [asset]
    section_result = Mock()
    section_result.scalars.return_value = section_scalars
    session.execute.side_effect = [scalar_result, list_result, section_result]
    repository = BrollAssetRepository(session)

    assert await repository.get(1) is asset
    assert await repository.get_by_collection_id(1) == [asset]
    assert await repository.get_by_section_order(1, 0) == [asset]

    collection_statement = str(session.execute.await_args_list[1].args[0])
    section_statement = str(session.execute.await_args_list[2].args[0])
    assert "broll_assets.script_section_order ASC NULLS LAST" in collection_statement
    assert "broll_assets.relevance_score DESC NULLS LAST" in collection_statement
    assert "broll_assets.id ASC" in collection_statement
    assert "broll_assets.relevance_score DESC NULLS LAST" in section_statement
    assert "broll_assets.id ASC" in section_statement


async def test_asset_save_and_commit(session: AsyncMock, asset: BrollAsset) -> None:
    repository = BrollAssetRepository(session)

    assert await repository.save(asset) is asset
    await repository.commit()

    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
