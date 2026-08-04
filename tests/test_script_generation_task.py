from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.core.exceptions import ScriptGenerationError, ScriptNotFoundError
from app.models.script import Script, ScriptStatus, ScriptTone
from app.services.script_generation_service import ScriptGenerationService
from app.tasks import script_generation as task_module


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_generation_task_composes_dependencies_and_returns_result(monkeypatch) -> None:
    session = FakeSession()
    dependencies = {}

    class FakeService:
        def __init__(
            self,
            video_repository,
            analysis_repository,
            script_repository,
            generator,
        ) -> None:
            dependencies.update(
                video_repository=video_repository,
                analysis_repository=analysis_repository,
                script_repository=script_repository,
                generator=generator,
            )

        async def process_script(self, script_id: int):
            dependencies["script_id"] = script_id
            return SimpleNamespace(id=script_id, status=ScriptStatus.COMPLETED)

    video_repository = object()
    analysis_repository = object()
    script_repository = object()
    generator = object()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: video_repository)
    monkeypatch.setattr(
        task_module, "VideoAnalysisRepository", lambda received: analysis_repository
    )
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: script_repository)
    monkeypatch.setattr(task_module, "LocalScriptGenerator", lambda: generator)
    monkeypatch.setattr(task_module, "ScriptGenerationService", FakeService)

    result = await task_module._run_generation(7)

    assert result == {"script_id": 7, "script_status": "completed"}
    assert dependencies == {
        "video_repository": video_repository,
        "analysis_repository": analysis_repository,
        "script_repository": script_repository,
        "generator": generator,
        "script_id": 7,
    }
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_sync_task_runs_async_helper(monkeypatch) -> None:
    async def fake_run(script_id: int) -> dict[str, int | str]:
        return {"script_id": script_id, "script_status": "completed"}

    monkeypatch.setattr(task_module, "_run_generation", fake_run)

    assert task_module.generate_script.run(7) == {
        "script_id": 7,
        "script_status": "completed",
    }
    assert task_module.generate_script.name == "scripts.generate"
    assert task_module.celery_app.tasks["scripts.generate"].name == "scripts.generate"


async def test_script_generation_error_commits_failed_state_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_script(self, script_id: int):
            raise ScriptGenerationError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoAnalysisRepository", lambda received: object())
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalScriptGenerator", object)
    monkeypatch.setattr(task_module, "ScriptGenerationService", FailingService)

    with pytest.raises(ScriptGenerationError):
        await task_module._run_generation(7)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_precondition_error_rolls_back_and_reraises(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_script(self, script_id: int):
            raise ScriptNotFoundError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoAnalysisRepository", lambda received: object())
    monkeypatch.setattr(task_module, "ScriptRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalScriptGenerator", object)
    monkeypatch.setattr(task_module, "ScriptGenerationService", FailingService)

    with pytest.raises(ScriptNotFoundError):
        await task_module._run_generation(7)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


async def test_completed_script_task_is_idempotent(monkeypatch) -> None:
    session = FakeSession()
    completed = Script(
        id=7,
        video_id="video-1",
        video_analysis_id=1,
        status=ScriptStatus.COMPLETED,
        target_duration_seconds=30,
        tone=ScriptTone.ENGAGING,
        language="en",
        generation_options={"target_duration_seconds": 30},
    )

    class CompletedScriptRepository:
        async def get(self, script_id: int) -> Script | None:
            return completed if script_id == completed.id else None

    class UnexpectedRepository:
        async def get(self, identifier):
            raise AssertionError("completed scripts must not load linked dependencies")

    class CountingGenerator:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, generation_input):
            self.calls += 1
            raise AssertionError("completed scripts must not invoke the generator")

    generator = CountingGenerator()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: UnexpectedRepository())
    monkeypatch.setattr(
        task_module, "VideoAnalysisRepository", lambda received: UnexpectedRepository()
    )
    monkeypatch.setattr(
        task_module, "ScriptRepository", lambda received: CompletedScriptRepository()
    )
    monkeypatch.setattr(task_module, "LocalScriptGenerator", lambda: generator)
    monkeypatch.setattr(task_module, "ScriptGenerationService", ScriptGenerationService)

    result = await task_module._run_generation(7)

    assert result == {"script_id": 7, "script_status": "completed"}
    assert generator.calls == 0
    assert session.commits == 1
    assert session.closed


def test_operational_error_uses_bounded_celery_retry(monkeypatch) -> None:
    async def fail_with_operational_error(script_id: int):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(task_module, "_run_generation", fail_with_operational_error)
    monkeypatch.setattr(task_module.generate_script, "retry", retry)

    with pytest.raises(Retry):
        task_module.generate_script.run(7)

    retry.assert_called_once()
    assert task_module.generate_script.autoretry_for == (OperationalError,)
    assert task_module.generate_script.retry_backoff is True
    assert task_module.generate_script.max_retries == 3
