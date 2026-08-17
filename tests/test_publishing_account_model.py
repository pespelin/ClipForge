from sqlalchemy import UniqueConstraint

from app.models.publish_job import PublishPlatform
from app.models.publishing_account import PublishingAccount


def test_account_stores_provider_neutral_identity_and_label() -> None:
    account = PublishingAccount(
        id=7,
        platform=PublishPlatform.YOUTUBE,
        remote_account_id="test-channel-id",
        display_name="Test Channel",
    )

    assert account.id == 7
    assert account.platform is PublishPlatform.YOUTUBE
    assert account.remote_account_id == "test-channel-id"
    assert account.display_name == "Test Channel"


def test_account_defaults_constraints_and_non_secret_columns() -> None:
    table = PublishingAccount.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    unique_constraint = next(
        constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    )

    assert table.c.is_active.default.arg is True
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
    assert [column.name for column in unique_constraint.columns] == [
        "platform",
        "remote_account_id",
    ]
    assert {
        "ck_publishing_accounts_platform",
        "ck_publishing_accounts_remote_account_id_non_empty",
        "ck_publishing_accounts_display_name_non_empty",
        "uq_publishing_accounts_platform_remote_account_id",
    } <= constraint_names
    assert set(table.c.keys()) == {
        "id",
        "platform",
        "remote_account_id",
        "display_name",
        "is_active",
        "created_at",
        "updated_at",
    }
