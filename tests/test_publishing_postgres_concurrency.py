import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import (
    PublishingExecutionLeaseUnavailableError,
    PublishingExecutionLockUnavailableError,
)
from app.models.publish_job import PublishJob, PublishPlatform, PublishStatus, PublishVisibility
from app.models.publishing_upload_session import PublishingUploadSession
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video import Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.models.video_render import VideoRender, VideoRenderStatus
from app.models.voice_track import VoiceTrack, VoiceTrackStatus
from app.repositories.publish_job_repository import PublishJobRepository
from app.repositories.publishing_upload_session_repository import (
    PublishingUploadSessionRepository,
)
from app.services.publishing_upload_session_service import PublishingUploadSessionService

pytestmark = pytest.mark.postgres_integration

EXPECTED_ALEMBIC_HEAD = "20260821_0011"
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class UnusedEncryptor:
    def encrypt(self, plaintext: str) -> str:
        raise AssertionError("lease tests must not encrypt")

    def decrypt(self, ciphertext: str) -> str:
        raise AssertionError("lease tests must not decrypt")


@dataclass(frozen=True, slots=True)
class PostgresRows:
    video_id: str
    job_a_id: int
    job_b_id: int


@pytest_asyncio.fixture
async def postgres_session_factory():
    raw_url = os.getenv("CLIPFORGE_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("CLIPFORGE_POSTGRES_TEST_URL is not configured")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not url.database or "test" not in url.database:
        pytest.skip("PostgreSQL integration URL must target an explicit asyncpg test database")

    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as error:
        await engine.dispose()
        pytest.skip(f"isolated PostgreSQL test database is unavailable: {type(error).__name__}")

    async with session_factory() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == EXPECTED_ALEMBIC_HEAD
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def postgres_rows(postgres_session_factory) -> AsyncIterator[PostgresRows]:
    video_id = str(uuid.uuid4())
    async with postgres_session_factory() as session:
        video = Video(id=video_id, filename="task-16f.mp4", status="uploaded")
        session.add(video)
        await session.flush()
        analysis = VideoAnalysis(
            video_id=video_id,
            status=AnalysisStatus.PENDING,
            topics=[],
            keywords=[],
            hook_candidates=[],
            clip_candidates=[],
        )
        session.add(analysis)
        await session.flush()
        script = Script(
            video_id=video_id,
            video_analysis_id=analysis.id,
            status=ScriptStatus.PENDING,
            target_duration_seconds=30,
            tone=ScriptTone.NEUTRAL,
            language="en",
            generation_options={},
            sections=[],
        )
        session.add(script)
        await session.flush()
        voice = VoiceTrack(
            script_id=script.id,
            status=VoiceTrackStatus.PENDING,
            provider="local",
            voice="test",
            generation_options={},
            segments=[],
        )
        session.add(voice)
        await session.flush()
        render = VideoRender(
            script_id=script.id,
            voice_track_id=voice.id,
            status=VideoRenderStatus.PENDING,
            subtitle_style={},
            render_options={},
            timeline_data=[],
        )
        session.add(render)
        await session.flush()
        jobs = [make_publish_job(render.id, "a"), make_publish_job(render.id, "b")]
        session.add_all(jobs)
        await session.commit()
        rows = PostgresRows(video_id, jobs[0].id, jobs[1].id)

    try:
        yield rows
    finally:
        async with postgres_session_factory() as session:
            await session.execute(
                delete(PublishingUploadSession).where(
                    PublishingUploadSession.publish_job_id.in_([rows.job_a_id, rows.job_b_id])
                )
            )
            await session.execute(delete(PublishJob).where(PublishJob.video_render_id == render.id))
            await session.execute(delete(VideoRender).where(VideoRender.id == render.id))
            await session.execute(delete(VoiceTrack).where(VoiceTrack.id == voice.id))
            await session.execute(delete(Script).where(Script.id == script.id))
            await session.execute(delete(VideoAnalysis).where(VideoAnalysis.id == analysis.id))
            await session.execute(delete(Video).where(Video.id == video_id))
            await session.commit()


def make_publish_job(video_render_id: int, suffix: str) -> PublishJob:
    return PublishJob(
        video_render_id=video_render_id,
        status=PublishStatus.PENDING,
        platform=PublishPlatform.YOUTUBE,
        account_reference=f"test-channel-{suffix}",
        title=f"PostgreSQL concurrency {suffix}",
        description=None,
        tags=[],
        category=None,
        visibility=PublishVisibility.PRIVATE,
        made_for_kids=False,
        notify_subscribers=False,
        language="en",
        publish_options={},
        source_storage_key=f"test/task-16f-{suffix}.mp4",
        source_checksum=None,
        source_file_size_bytes=10,
        source_duration_seconds=1,
        provider_metadata={},
    )


async def assert_waiting(task: asyncio.Task) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.1)


async def test_same_job_lock_waits_for_commit_and_sees_committed_state(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    acquired = asyncio.Event()

    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        job_a = await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        assert job_a is not None

        async def acquire_second():
            acquired.set()
            return await PublishJobRepository(session_b).get_for_update(postgres_rows.job_a_id)

        waiting = asyncio.create_task(acquire_second())
        await acquired.wait()
        await assert_waiting(waiting)
        job_a.error_message = "committed-by-first-transaction"
        await session_a.commit()
        job_b = await asyncio.wait_for(waiting, timeout=2)
        assert job_b is not None
        assert job_b.error_message == "committed-by-first-transaction"
        await session_b.rollback()


async def test_rollback_releases_lock_and_discards_mutation(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        job_a = await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        assert job_a is not None
        job_a.error_message = "must-roll-back"
        waiting = asyncio.create_task(
            PublishJobRepository(session_b).get_for_update(postgres_rows.job_a_id)
        )
        await assert_waiting(waiting)
        await session_a.rollback()
        job_b = await asyncio.wait_for(waiting, timeout=2)
        assert job_b is not None
        assert job_b.error_message is None
        await session_b.rollback()


async def test_different_job_rows_do_not_block(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        assert await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        other = await asyncio.wait_for(
            PublishJobRepository(session_b).get_for_update(postgres_rows.job_b_id),
            timeout=1,
        )
        assert other is not None
        await session_a.rollback()
        await session_b.rollback()


async def test_real_lock_timeout_is_safe_and_requires_caller_rollback(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        assert await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        with pytest.raises(PublishingExecutionLockUnavailableError) as caught:
            await asyncio.wait_for(
                PublishJobRepository(session_b).get_for_update(postgres_rows.job_a_id),
                timeout=7,
            )
        assert str(caught.value) == "Publishing execution is temporarily busy"
        assert "SELECT" not in repr(caught.value)
        with pytest.raises(DBAPIError) as aborted:
            await session_b.execute(select(PublishJob.id))
        assert getattr(aborted.value.orig, "sqlstate", None) == "25P02"
        await session_b.rollback()
        assert await session_b.scalar(text("SELECT 1")) == 1
        await session_a.rollback()


async def test_checkpoint_is_visible_to_waiter_after_claimant_commit(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        assert await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        checkpoint_repository_a = PublishingUploadSessionRepository(session_a)
        assert await checkpoint_repository_a.get_by_publish_job_id(postgres_rows.job_a_id) is None
        await checkpoint_repository_a.create(make_checkpoint(postgres_rows.job_a_id))
        waiting = asyncio.create_task(
            PublishJobRepository(session_b).get_for_update(postgres_rows.job_a_id)
        )
        await assert_waiting(waiting)
        await session_a.commit()
        assert await asyncio.wait_for(waiting, timeout=2)
        checkpoint = await PublishingUploadSessionRepository(session_b).get_by_publish_job_id(
            postgres_rows.job_a_id
        )
        assert checkpoint is not None
        assert checkpoint.next_byte_offset == 0
        await session_b.rollback()


async def test_active_execution_lease_cannot_be_overwritten_after_serialized_lock(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    expiry_a = NOW + timedelta(minutes=15)
    async with postgres_session_factory() as setup:
        checkpoint = make_checkpoint(postgres_rows.job_a_id)
        checkpoint.execution_owner = "owner-a"
        checkpoint.execution_lease_expires_at = expiry_a
        await PublishingUploadSessionRepository(setup).create(checkpoint)
        await setup.commit()

    async with (
        postgres_session_factory() as session_a,
        postgres_session_factory() as session_b,
    ):
        assert await PublishJobRepository(session_a).get_for_update(postgres_rows.job_a_id)
        waiting = asyncio.create_task(
            PublishJobRepository(session_b).get_for_update(postgres_rows.job_a_id)
        )
        await assert_waiting(waiting)
        await session_a.commit()
        assert await asyncio.wait_for(waiting, timeout=2)
        service = PublishingUploadSessionService(
            PublishingUploadSessionRepository(session_b), UnusedEncryptor()
        )
        with pytest.raises(PublishingExecutionLeaseUnavailableError):
            await service.acquire_execution_lease(
                postgres_rows.job_a_id,
                owner="owner-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(minutes=30),
            )
        await session_b.rollback()

    async with postgres_session_factory() as verify:
        checkpoint = await PublishingUploadSessionRepository(verify).get_by_publish_job_id(
            postgres_rows.job_a_id
        )
        assert checkpoint is not None
        assert checkpoint.execution_owner == "owner-a"
        assert checkpoint.execution_lease_expires_at == expiry_a


async def test_expired_execution_lease_takeover_persists(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    new_expiry = NOW + timedelta(minutes=15)
    async with postgres_session_factory() as setup:
        checkpoint = make_checkpoint(postgres_rows.job_a_id)
        checkpoint.execution_owner = "owner-a"
        checkpoint.execution_lease_expires_at = NOW
        await PublishingUploadSessionRepository(setup).create(checkpoint)
        await setup.commit()

    async with postgres_session_factory() as claimant:
        assert await PublishJobRepository(claimant).get_for_update(postgres_rows.job_a_id)
        service = PublishingUploadSessionService(
            PublishingUploadSessionRepository(claimant), UnusedEncryptor()
        )
        await service.acquire_execution_lease(
            postgres_rows.job_a_id,
            owner="owner-b",
            now=NOW,
            lease_expires_at=new_expiry,
        )
        await claimant.commit()

    async with postgres_session_factory() as verify:
        checkpoint = await PublishingUploadSessionRepository(verify).get_by_publish_job_id(
            postgres_rows.job_a_id
        )
        assert checkpoint is not None
        assert checkpoint.execution_owner == "owner-b"
        assert checkpoint.execution_lease_expires_at == new_expiry


async def test_set_local_lock_timeout_does_not_leak_after_commit(
    postgres_session_factory,
    postgres_rows: PostgresRows,
) -> None:
    async with postgres_session_factory() as session:
        original = await session.scalar(text("SHOW lock_timeout"))
        assert await PublishJobRepository(session).get_for_update(postgres_rows.job_a_id)
        assert await session.scalar(text("SHOW lock_timeout")) == "5s"
        await session.commit()
        assert await session.scalar(text("SHOW lock_timeout")) == original


def make_checkpoint(publish_job_id: int) -> PublishingUploadSession:
    return PublishingUploadSession(
        publish_job_id=publish_job_id,
        platform=PublishPlatform.YOUTUBE,
        encrypted_session_uri="encrypted-test-session",
        total_bytes=10,
        next_byte_offset=0,
    )
