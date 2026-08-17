from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_credential import OAuthCredential
from app.repositories.oauth_credential_repository import OAuthCredentialRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def credential() -> OAuthCredential:
    return OAuthCredential(
        publishing_account_id=7,
        encrypted_access_token="encrypted-access",
        encrypted_refresh_token="encrypted-refresh",
    )


async def test_create_adds_and_flushes_without_transaction(
    session: AsyncMock, credential: OAuthCredential
) -> None:
    result = await OAuthCredentialRepository(session).create(credential)

    session.add.assert_called_once_with(credential)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    assert result is credential


async def test_save_flushes_without_transaction(
    session: AsyncMock, credential: OAuthCredential
) -> None:
    result = await OAuthCredentialRepository(session).save(credential)

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    assert result is credential


@pytest.mark.parametrize(
    ("method", "arguments", "expected_where"),
    [
        ("get_by_id", (3,), "oauth_credentials.id"),
        (
            "get_by_publishing_account_id",
            (7,),
            "oauth_credentials.publishing_account_id",
        ),
    ],
)
async def test_get_queries_return_matching_credential(
    session: AsyncMock,
    credential: OAuthCredential,
    method: str,
    arguments: tuple,
    expected_where: str,
) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = credential
    session.execute.return_value = query_result

    result = await getattr(OAuthCredentialRepository(session), method)(*arguments)

    assert expected_where in str(session.execute.await_args.args[0])
    assert result is credential
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
