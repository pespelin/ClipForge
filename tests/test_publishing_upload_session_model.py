from sqlalchemy import BigInteger, String, Text, inspect

from app.models.publish_job import PublishJob, PublishPlatform
from app.models.publishing_upload_session import PublishingUploadSession
from app.schemas.publish_job import PublishJobResponse


def test_publish_job_owns_at_most_one_upload_session() -> None:
    job_relationship = inspect(PublishJob).relationships.upload_session
    session_relationship = inspect(PublishingUploadSession).relationships.publish_job

    assert job_relationship.uselist is False
    assert "delete-orphan" in job_relationship.cascade
    assert job_relationship.passive_deletes is True
    assert session_relationship.uselist is False
    assert session_relationship.back_populates == "upload_session"


def test_upload_session_columns_and_security_constraints() -> None:
    table = PublishingUploadSession.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    foreign_key = next(iter(table.c.publish_job_id.foreign_keys))

    assert set(table.c.keys()) == {
        "id",
        "publish_job_id",
        "platform",
        "encrypted_session_uri",
        "total_bytes",
        "next_byte_offset",
        "execution_owner",
        "execution_lease_expires_at",
        "created_at",
        "updated_at",
    }
    assert isinstance(table.c.encrypted_session_uri.type, Text)
    assert isinstance(table.c.total_bytes.type, BigInteger)
    assert isinstance(table.c.next_byte_offset.type, BigInteger)
    assert table.c.next_byte_offset.default.arg == 0
    assert isinstance(table.c.execution_owner.type, String)
    assert table.c.execution_owner.type.length == 255
    assert table.c.execution_owner.nullable is True
    assert table.c.execution_lease_expires_at.nullable is True
    assert table.c.execution_lease_expires_at.type.timezone is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert foreign_key.target_fullname == "publish_jobs.id"
    assert foreign_key.ondelete == "CASCADE"
    assert table.c.platform.type.enum_class is PublishPlatform
    assert {
        "ck_publishing_upload_sessions_platform",
        "ck_publishing_upload_sessions_session_uri_non_empty",
        "ck_publishing_upload_sessions_total_bytes_positive",
        "ck_publishing_upload_sessions_offset_in_range",
        "ck_publishing_upload_sessions_execution_lease_paired",
        "uq_publishing_upload_sessions_publish_job_id",
    } <= constraint_names
    assert "session_uri" not in table.c
    assert "access_token" not in table.c
    assert "refresh_token" not in table.c
    assert "execution_owner" not in PublishJobResponse.model_fields
    assert "execution_lease_expires_at" not in PublishJobResponse.model_fields


def test_upload_session_association_is_bidirectional() -> None:
    publish_job = PublishJob(account_reference="channel", title="Title")
    upload_session = PublishingUploadSession(
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted-session-uri",
        total_bytes=10,
    )

    publish_job.upload_session = upload_session

    assert upload_session.publish_job is publish_job
    assert publish_job.upload_session is upload_session
