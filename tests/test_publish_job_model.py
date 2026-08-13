from sqlalchemy import JSON, inspect

from app.models.publish_job import (
    PublishJob,
    PublishPlatform,
    PublishStatus,
    PublishVisibility,
)
from app.models.video_render import VideoRender


def test_render_to_publish_job_is_restrictive_one_to_many() -> None:
    render_jobs = inspect(VideoRender).relationships.publish_jobs
    job_render = inspect(PublishJob).relationships.video_render

    assert render_jobs.uselist is True
    assert render_jobs.passive_deletes == "all"
    assert "delete" not in render_jobs.cascade
    assert "delete-orphan" not in render_jobs.cascade
    assert job_render.uselist is False


def test_render_supports_multiple_publish_variants() -> None:
    video_render = VideoRender(id=1, script_id=2, voice_track_id=3)
    first = PublishJob(video_render=video_render, account_reference="channel-a", title="First")
    second = PublishJob(video_render=video_render, account_reference="channel-b", title="Second")

    assert video_render.publish_jobs == [first, second]
    assert first.video_render is video_render
    assert second.video_render is video_render


def test_defaults_json_artifact_columns_constraints_and_deletion_policy() -> None:
    table = PublishJob.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    foreign_key = next(iter(table.c.video_render_id.foreign_keys))

    assert table.c.status.default.arg is PublishStatus.PENDING
    assert table.c.platform.default.arg is PublishPlatform.YOUTUBE
    assert table.c.visibility.default.arg is PublishVisibility.PRIVATE
    assert table.c.made_for_kids.default.arg is False
    assert table.c.notify_subscribers.default.arg is False
    assert isinstance(table.c.tags.type, JSON)
    assert isinstance(table.c.publish_options.type, JSON)
    assert isinstance(table.c.provider_metadata.type, JSON)
    assert table.c.source_storage_key.nullable is False
    assert table.c.source_file_size_bytes.nullable is False
    assert table.c.source_duration_seconds.nullable is False
    assert table.c.video_render_id.unique is not True
    assert foreign_key.ondelete == "RESTRICT"
    assert {
        "ck_publish_jobs_status",
        "ck_publish_jobs_platform",
        "ck_publish_jobs_visibility",
        "ck_publish_jobs_account_reference_non_empty",
        "ck_publish_jobs_title_non_empty",
        "ck_publish_jobs_category_non_empty",
        "ck_publish_jobs_source_storage_key_non_empty",
        "ck_publish_jobs_source_checksum_non_empty",
        "ck_publish_jobs_source_file_size_non_negative",
        "ck_publish_jobs_source_duration_positive",
        "ck_publish_jobs_remote_media_id_non_empty",
        "ck_publish_jobs_remote_url_non_empty",
        "ck_publish_jobs_published_content",
    } <= constraint_names
