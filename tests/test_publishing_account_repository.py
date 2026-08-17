from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publish_job import PublishPlatform
from app.models.publishing_account import PublishingAccount
from app.repositories.publishing_account_repository import PublishingAccountRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def account() -> PublishingAccount:
    return PublishingAccount(
        platform=PublishPlatform.YOUTUBE,
        remote_account_id="test-channel-id",
        display_name="Test Channel",
    )


async def test_create_adds_and_flushes_without_transaction(
    session: AsyncMock, account: PublishingAccount
) -> None:
    result = await PublishingAccountRepository(session).create(account)

    session.add.assert_called_once_with(account)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    assert result is account


async def test_save_flushes_without_transaction(
    session: AsyncMock, account: PublishingAccount
) -> None:
    result = await PublishingAccountRepository(session).save(account)

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    assert result is account


@pytest.mark.parametrize(
    ("method", "arguments", "expected_where"),
    [
        ("get_by_id", (7,), "publishing_accounts.id"),
        (
            "get_by_platform_and_remote_account_id",
            (PublishPlatform.YOUTUBE, "test-channel-id"),
            "publishing_accounts.platform",
        ),
    ],
)
async def test_get_queries_return_matching_account(
    session: AsyncMock,
    account: PublishingAccount,
    method: str,
    arguments: tuple,
    expected_where: str,
) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = account
    session.execute.return_value = query_result
    repository = PublishingAccountRepository(session)

    result = await getattr(repository, method)(*arguments)

    statement = str(session.execute.await_args.args[0])
    assert expected_where in statement
    if method == "get_by_platform_and_remote_account_id":
        assert "publishing_accounts.remote_account_id" in statement
    assert result is account
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
