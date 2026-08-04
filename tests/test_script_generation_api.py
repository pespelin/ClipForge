from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_script_generation_service
from app.api.v1.endpoints import scripts as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    CompletedAnalysisNotFoundError,
    InvalidClipCandidateIndexError,
    InvalidHookCandidateIndexError,
    ScriptNotFoundError,
    VideoNotFoundError,
)
from app.models.script import ScriptStatus


def script(state: ScriptStatus, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "video_id": "video-1",
        "video_analysis_id": 7,
        "status": state,
        "title": None,
        "hook": None,
        "body": None,
        "call_to_action": None,
        "full_script": None,
        "estimated_duration_seconds": None,
        "target_duration_seconds": 30.0,
        "tone": "engaging",
        "language": "en",
        "generation_options": {
            "target_duration_seconds": 30.0,
            "tone": "engaging",
            "language": "en",
            "include_call_to_action": True,
            "preferred_hook_candidate_index": None,
            "preferred_clip_candidate_index": None,
        },
        "sections": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def completed_script(**values):
    defaults = {
        "title": "Editing that keeps attention",
        "hook": "Your first edit decides everything.",
        "body": "Start with the strongest visual and remove every pause.",
        "call_to_action": "Follow for more editing tips.",
        "full_script": (
            "Your first edit decides everything. Start with the strongest visual and "
            "remove every pause. Follow for more editing tips."
        ),
        "estimated_duration_seconds": 18.0,
        "sections": [
            {
                "order": 0,
                "type": "hook",
                "text": "Your first edit decides everything.",
                "estimated_duration_seconds": 3.0,
                "source_start_time": 0.0,
                "source_end_time": 3.0,
            }
        ],
        "completed_at": datetime.now(UTC),
    }
    defaults.update(values)
    return script(ScriptStatus.COMPLETED, **defaults)


class FakeService:
    def __init__(self, current=None, *, scripts=None, should_enqueue=True) -> None:
        self.current = current
        self.scripts = scripts or []
        self.should_enqueue = should_enqueue
        self.created_options = None
        self.enqueue_failures = []

    async def request_script_generation(self, video_id, options):
        self.created_options = options
        return self.current

    async def get_script(self, script_id):
        return self.current

    async def list_scripts_for_video(self, video_id):
        return self.scripts

    async def prepare_script_retry(self, script_id):
        if self.should_enqueue:
            self.current.status = ScriptStatus.PENDING
            self.current.completed_at = None
            self.current.error_message = None
        return self.current, self.should_enqueue

    async def mark_script_enqueue_failed(self, current, error):
        current.status = ScriptStatus.FAILED
        current.completed_at = None
        current.error_message = f"Script task enqueue failed: {error}"
        self.enqueue_failures.append((current, error))


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_script_generation_service] = lambda: service
    return TestClient(app)


def options(**values) -> dict:
    result = {
        "target_duration_seconds": 30,
        "tone": "engaging",
        "language": "en",
        "include_call_to_action": True,
        "preferred_hook_candidate_index": None,
        "preferred_clip_candidate_index": None,
    }
    result.update(values)
    return result


def test_create_queues_pending_script_and_returns_202(monkeypatch) -> None:
    current = script(ScriptStatus.PENDING, id=12)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_script, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/scripts", json=options())

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert service.created_options.target_duration_seconds == 30
    assert queued == [12]


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (VideoNotFoundError(), 404),
        (CompletedAnalysisNotFoundError(), 409),
        (InvalidHookCandidateIndexError(), 422),
        (InvalidClipCandidateIndexError(), 422),
    ],
)
def test_create_maps_service_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_script_generation(self, video_id, options):
            raise error

    with client_for(FailingService()) as client:
        response = client.post("/api/v1/videos/video-1/scripts", json=options())

    assert response.status_code == expected_status


def test_create_rejects_invalid_request_schema() -> None:
    with client_for(FakeService()) as client:
        response = client.post(
            "/api/v1/videos/video-1/scripts",
            json=options(target_duration_seconds=0),
        )

    assert response.status_code == 422


def test_create_enqueue_failure_returns_503_and_persists_failure(monkeypatch) -> None:
    current = script(ScriptStatus.PENDING)
    service = FakeService(current)

    def fail(script_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.generate_script, "delay", fail)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/scripts", json=options())

    assert response.status_code == 503
    assert response.json() == {"detail": "Script generation could not be queued"}
    assert current.status == ScriptStatus.FAILED
    assert current.error_message == "Script task enqueue failed: broker unavailable"
    assert service.enqueue_failures[0][0] is current


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (ScriptStatus.PENDING, None),
        (ScriptStatus.GENERATING, None),
        (ScriptStatus.FAILED, "Generation failed"),
    ],
)
def test_get_incomplete_script_returns_status(state, message) -> None:
    with client_for(FakeService(script(state, error_message=message))) as client:
        response = client.get("/api/v1/scripts/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "video_id": "video-1",
        "video_analysis_id": 7,
        "status": state.value,
        "completed_at": None,
        "error_message": message,
    }


def test_get_completed_script_returns_full_response() -> None:
    with client_for(FakeService(completed_script())) as client:
        response = client.get("/api/v1/scripts/1")

    body = response.json()
    assert response.status_code == 200
    assert body["title"] == "Editing that keeps attention"
    assert body["generation_options"]["tone"] == "engaging"
    assert body["sections"][0]["type"] == "hook"


def test_get_missing_script_returns_404() -> None:
    class MissingService(FakeService):
        async def get_script(self, script_id):
            raise ScriptNotFoundError

    with client_for(MissingService()) as client:
        response = client.get("/api/v1/scripts/999")

    assert response.status_code == 404


def test_list_returns_mixed_variants_in_service_order() -> None:
    now = datetime.now(UTC)
    newest = script(ScriptStatus.PENDING, id=2, created_at=now)
    oldest = completed_script(id=1, created_at=now - timedelta(minutes=1))

    with client_for(FakeService(scripts=[newest, oldest])) as client:
        response = client.get("/api/v1/videos/video-1/scripts")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2, 1]
    assert "title" not in response.json()[0]
    assert response.json()[1]["title"] == "Editing that keeps attention"


def test_list_allows_empty_result() -> None:
    with client_for(FakeService(scripts=[])) as client:
        response = client.get("/api/v1/videos/video-1/scripts")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("state", [ScriptStatus.PENDING, ScriptStatus.FAILED])
def test_retry_reenqueues_same_row_and_clears_error(monkeypatch, state) -> None:
    current = script(state, id=9, error_message="Old error")
    service = FakeService(current, should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_script, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/9/retry")

    assert response.status_code == 202
    assert response.json()["id"] == 9
    assert response.json()["status"] == "pending"
    assert current.error_message is None
    assert queued == [9]


@pytest.mark.parametrize("state", [ScriptStatus.GENERATING, ScriptStatus.COMPLETED])
def test_retry_does_not_enqueue_active_or_completed_script(monkeypatch, state) -> None:
    current = completed_script() if state == ScriptStatus.COMPLETED else script(state)
    service = FakeService(current, should_enqueue=False)
    queued = []
    monkeypatch.setattr(endpoint_module.generate_script, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == state.value
    assert queued == []


def test_retry_enqueue_failure_returns_503(monkeypatch) -> None:
    current = script(ScriptStatus.FAILED, error_message="Old error")
    service = FakeService(current, should_enqueue=True)

    def fail(script_id: int) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.generate_script, "delay", fail)

    with client_for(service) as client:
        response = client.post("/api/v1/scripts/1/retry")

    assert response.status_code == 503
    assert current.status == ScriptStatus.FAILED
    assert current.error_message == "Script task enqueue failed: broker unavailable"
