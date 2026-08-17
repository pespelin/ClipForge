from sqlalchemy import inspect

from app.models.oauth_credential import OAuthCredential
from app.models.publishing_account import PublishingAccount


def test_account_credential_relationship_is_one_to_one_owned() -> None:
    account_relationship = inspect(PublishingAccount).relationships.oauth_credential
    credential_relationship = inspect(OAuthCredential).relationships.publishing_account

    assert account_relationship.uselist is False
    assert "delete-orphan" in account_relationship.cascade
    assert credential_relationship.uselist is False
    assert credential_relationship.back_populates == "oauth_credential"


def test_credential_columns_enforce_encrypted_one_per_account_storage() -> None:
    table = OAuthCredential.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    foreign_key = next(iter(table.c.publishing_account_id.foreign_keys))

    assert set(table.c.keys()) == {
        "id",
        "publishing_account_id",
        "encrypted_access_token",
        "encrypted_refresh_token",
        "token_type",
        "scope",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert table.c.encrypted_access_token.nullable is False
    assert table.c.encrypted_refresh_token.nullable is True
    assert table.c.expires_at.type.timezone is True
    assert foreign_key.target_fullname == "publishing_accounts.id"
    assert foreign_key.ondelete == "CASCADE"
    assert {
        "ck_oauth_credentials_access_token_non_empty",
        "ck_oauth_credentials_refresh_token_non_empty",
        "ck_oauth_credentials_token_type_non_empty",
        "ck_oauth_credentials_scope_non_empty",
        "uq_oauth_credentials_publishing_account_id",
    } <= constraint_names
    assert "access_token" not in table.c
    assert "refresh_token" not in table.c
