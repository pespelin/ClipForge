import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_script_generation_service
from app.api.v1.endpoints import scripts as scripts_endpoint
from app.api.v1.router import router
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import ScriptGenerationError
from app.models.script import Script, ScriptStatus
from app.models.video import Transcript, Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.providers.script import LocalScriptGenerator
from app.schemas.script import ScriptResponse, ScriptStatusResponse
from app.services.script_generation_service import ScriptGenerationService
from app.tasks import script_generation as script_task


class WorkflowState:
    def __init__(self) -> None:
        self.video = Video(
            id="video-1",
            filename="source.mp4",
            status="completed",
            duration=60.0,
            metadata_data={
                "duration": 60.0,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "file_size": 1024,
            },
        )
        self.video.transcript = Transcript(
            language="en",
            full_text=(
                "Great editing starts with a clear story. Open with the strongest visual, "
                "remove unnecessary pauses, and give every scene a purpose."
            ),
        )
        self.analysis = VideoAnalysis(
            id=7,
            video_id=self.video.id,
            status=AnalysisStatus.COMPLETED,
            summary="Clear editing keeps viewers focused on a strong story.",
            topics=[{"name": "Video Editing", "description": None, "relevance": 0.95}],
            keywords=["editing", "story", "viewers"],
            sentiment="positive",
            hook_candidates=[
                {
                    "text": "Your first edit decides whether viewers stay.",
                    "start_time": 0.0,
                    "end_time": 3.0,
                    "reason": "Strong opening",
                    "score": 0.9,
                }
            ],
            clip_candidates=[
                {
                    "title": "The strongest editing lesson",
                    "start_time": 3.0,
                    "end_time": 25.0,
                    "reason": "Compact explanation",
                    "score": 0.85,
                }
            ],
            completed_at=datetime.now(UTC),
        )
        self.scripts: dict[int, Script] = {}
        self.next_script_id = 1
        self.events: list[str] = []
        self.saved_statuses: list[tuple[int, ScriptStatus]] = []


class InMemorySession:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closes += 1

    async def commit(self) -> None:
        self.commits += 1
        self.state.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.state.events.append("rollback")


class InMemoryVideoRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, video_id: str) -> Video | None:
        return self.state.video if self.state.video.id == video_id else None


class InMemoryAnalysisRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.state = session.state

    async def get(self, analysis_id: int) -> VideoAnalysis | None:
        return self.state.analysis if self.state.analysis.id == analysis_id else None

    async def get_by_video_id(self, video_id: str) -> VideoAnalysis | None:
        return self.state.analysis if self.state.analysis.video_id == video_id else None


class InMemoryScriptRepository:
    def __init__(self, session: InMemorySession) -> None:
        self.session = session
        self.state = session.state

    async def create(self, script: Script) -> Script:
        script.id = self.state.next_script_id
        self.state.next_script_id += 1
        timestamp = datetime.now(UTC)
        script.created_at = timestamp
        script.updated_at = timestamp
        self.state.scripts[script.id] = script
        self.state.events.append(f"create:{script.id}")
        return script

    async def get(self, script_id: int) -> Script | None:
        return self.state.scripts.get(script_id)

    async def get_by_video_id(self, video_id: str) -> list[Script]:
        rows = [row for row in self.state.scripts.values() if row.video_id == video_id]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    async def save(self, script: Script) -> Script:
        script.updated_at = datetime.now(UTC)
        self.state.scripts[script.id] = script
        self.state.saved_statuses.append((script.id, script.status))
        self.state.events.append(f"save:{script.id}:{script.status.value}")
        return script

    async def commit(self) -> None:
        await self.session.commit()


class CountingLocalScriptGenerator(LocalScriptGenerator):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, generation_input):
        self.calls += 1
        return await super().generate(generation_input)


class FailingScriptGenerator:
    async def generate(self, generation_input):
        raise RuntimeError("local generation failed")


class Phase4Harness:
    def __init__(self, monkeypatch) -> None:
        self.state = WorkflowState()
        self.session = InMemorySession(self.state)
        self.generator = CountingLocalScriptGenerator()
        self.queued: list[int] = []
        self.publication_commits: list[int] = []
        self.publication_states: list[ScriptStatus] = []
        self.video_repository = InMemoryVideoRepository(self.session)
        self.analysis_repository = InMemoryAnalysisRepository(self.session)
        self.script_repository = InMemoryScriptRepository(self.session)
        self.service = self._service(self.generator)

        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_script_generation_service] = lambda: self.service

        def publish(script_id: int) -> None:
            assert self.state.events[-1] == "commit"
            self.publication_commits.append(self.session.commits)
            self.publication_states.append(self.state.scripts[script_id].status)
            self.queued.append(script_id)

        monkeypatch.setattr(scripts_endpoint.generate_script, "delay", publish)
        monkeypatch.setattr(script_task, "AsyncSessionLocal", lambda: self.session)
        monkeypatch.setattr(script_task, "VideoRepository", InMemoryVideoRepository)
        monkeypatch.setattr(script_task, "VideoAnalysisRepository", InMemoryAnalysisRepository)
        monkeypatch.setattr(script_task, "ScriptRepository", InMemoryScriptRepository)
        monkeypatch.setattr(script_task, "LocalScriptGenerator", lambda: self.generator)

    def _service(self, generator) -> ScriptGenerationService:
        return ScriptGenerationService(
            self.video_repository,
            self.analysis_repository,
            self.script_repository,
            generator,
        )

    def client(self) -> TestClient:
        return TestClient(self.app)

    def run_task(self, script_id: int) -> dict[str, int | str]:
        return asyncio.run(script_task._run_generation(script_id))


def request_options(**values) -> dict:
    result = {
        "target_duration_seconds": 30,
        "tone": "engaging",
        "language": "en",
        "include_call_to_action": True,
        "preferred_hook_candidate_index": 0,
        "preferred_clip_candidate_index": 0,
    }
    result.update(values)
    return result


def test_phase3_to_phase4_workflow_variants_and_idempotency(monkeypatch) -> None:
    harness = Phase4Harness(monkeypatch)

    with harness.client() as client:
        first_response = client.post("/api/v1/videos/video-1/scripts", json=request_options())
        assert first_response.status_code == 202
        first_pending = ScriptStatusResponse.model_validate(first_response.json())
        assert first_pending.status == ScriptStatus.PENDING
        assert harness.queued == [first_pending.id]
        assert harness.publication_commits == [1]
        assert harness.publication_states == [ScriptStatus.PENDING]

        first_task_result = harness.run_task(harness.queued.pop())
        assert first_task_result == {
            "script_id": first_pending.id,
            "script_status": "completed",
        }

        completed_response = client.get(f"/api/v1/scripts/{first_pending.id}")
        first_completed = ScriptResponse.model_validate(completed_response.json())
        assert completed_response.status_code == 200
        assert first_completed.status == ScriptStatus.COMPLETED
        assert first_completed.title
        assert first_completed.full_script
        assert first_completed.sections

        second_response = client.post(
            "/api/v1/videos/video-1/scripts",
            json=request_options(
                target_duration_seconds=45,
                tone="educational",
                include_call_to_action=False,
            ),
        )
        second_pending = ScriptStatusResponse.model_validate(second_response.json())
        assert second_response.status_code == 202
        assert second_pending.id != first_pending.id
        assert len(harness.state.scripts) == 2

        first_row = harness.state.scripts[first_pending.id]
        second_row = harness.state.scripts[second_pending.id]
        assert first_row.video_analysis_id == second_row.video_analysis_id == 7
        assert first_row.generation_options["target_duration_seconds"] == 30
        assert second_row.generation_options["target_duration_seconds"] == 45
        assert first_row.generation_options["include_call_to_action"] is True
        assert second_row.generation_options["include_call_to_action"] is False

        mixed_response = client.get("/api/v1/videos/video-1/scripts")
        mixed = mixed_response.json()
        assert mixed_response.status_code == 200
        assert [item["id"] for item in mixed] == [second_pending.id, first_pending.id]
        ScriptStatusResponse.model_validate(mixed[0])
        ScriptResponse.model_validate(mixed[1])

        harness.run_task(harness.queued.pop())
        assert harness.generator.calls == 2
        repeated_result = harness.run_task(first_pending.id)
        assert repeated_result["script_status"] == "completed"
        assert harness.generator.calls == 2

        queued_before_retry = list(harness.queued)
        retry_response = client.post(f"/api/v1/scripts/{first_pending.id}/retry")
        assert retry_response.status_code == 200
        ScriptResponse.model_validate(retry_response.json())
        assert harness.queued == queued_before_retry
        assert len(harness.state.scripts) == 2

        final_list = client.get("/api/v1/videos/video-1/scripts")
        assert len(final_list.json()) == 2
        assert all(item["status"] == "completed" for item in final_list.json())


def test_generation_failure_get_retry_and_same_row_recovery(monkeypatch) -> None:
    harness = Phase4Harness(monkeypatch)

    with harness.client() as client:
        create_response = client.post("/api/v1/videos/video-1/scripts", json=request_options())
        script_id = ScriptStatusResponse.model_validate(create_response.json()).id
        harness.queued.clear()
        harness.generator = FailingScriptGenerator()

        with pytest.raises(ScriptGenerationError):
            harness.run_task(script_id)

        failed_row = harness.state.scripts[script_id]
        assert failed_row.status == ScriptStatus.FAILED
        assert failed_row.completed_at is None
        assert failed_row.error_message == "local generation failed"
        assert harness.state.events[-1] == "commit"

        failed_response = client.get(f"/api/v1/scripts/{script_id}")
        failed = ScriptStatusResponse.model_validate(failed_response.json())
        assert failed_response.status_code == 200
        assert failed.status == ScriptStatus.FAILED
        assert failed.error_message == "local generation failed"

        commits_before_retry = harness.session.commits
        retry_response = client.post(f"/api/v1/scripts/{script_id}/retry")
        retry = ScriptStatusResponse.model_validate(retry_response.json())
        assert retry_response.status_code == 202
        assert retry.id == script_id
        assert retry.status == ScriptStatus.PENDING
        assert retry.error_message is None
        assert harness.publication_commits[-1] == commits_before_retry + 1
        assert harness.queued == [script_id]
        assert len(harness.state.scripts) == 1

        harness.generator = CountingLocalScriptGenerator()
        recovery_result = harness.run_task(harness.queued.pop())
        assert recovery_result == {"script_id": script_id, "script_status": "completed"}
        assert harness.generator.calls == 1
        recovered = ScriptResponse.model_validate(client.get(f"/api/v1/scripts/{script_id}").json())
        assert recovered.status == ScriptStatus.COMPLETED
        assert len(harness.state.scripts) == 1


def test_broker_failure_persists_same_row_and_returns_503(monkeypatch) -> None:
    harness = Phase4Harness(monkeypatch)

    def fail_publish(script_id: int) -> None:
        assert harness.state.events[-1] == "commit"
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(scripts_endpoint.generate_script, "delay", fail_publish)

    with harness.client() as client:
        response = client.post("/api/v1/videos/video-1/scripts", json=request_options())

        assert response.status_code == 503
        assert response.json() == {"detail": "Script generation could not be queued"}
        assert len(harness.state.scripts) == 1
        failed_row = next(iter(harness.state.scripts.values()))
        assert failed_row.status == ScriptStatus.FAILED
        assert failed_row.error_message == "Script task enqueue failed: broker unavailable"
        assert harness.state.events[-2:] == [
            f"save:{failed_row.id}:failed",
            "commit",
        ]

        failed = ScriptStatusResponse.model_validate(
            client.get(f"/api/v1/scripts/{failed_row.id}").json()
        )
        assert failed.status == ScriptStatus.FAILED
