from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_video_analysis_service
from app.api.v1.endpoints import video_analysis as endpoint_module
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    AnalysisNotFoundError,
    TranscriptNotReadyError,
    VideoNotFoundError,
)
from app.models.video_analysis import AnalysisStatus


def analysis(status: AnalysisStatus, **values):
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "video_id": "video-1",
        "status": status,
        "summary": None,
        "topics": [],
        "keywords": [],
        "sentiment": None,
        "hook_candidates": [],
        "clip_candidates": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def client_for(service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(endpoint_module.router, prefix="/api/v1")
    app.dependency_overrides[get_video_analysis_service] = lambda: service
    return TestClient(app)


class FakeService:
    def __init__(self, current, *, should_enqueue: bool = False) -> None:
        self.current = current
        self.should_enqueue = should_enqueue
        self.request_calls = 0
        self.get_calls = 0
        self.enqueue_failures = []

    async def request_analysis(self, video_id: str):
        self.request_calls += 1
        return self.current, self.should_enqueue

    async def get_analysis(self, video_id: str):
        self.get_calls += 1
        return self.current

    async def mark_enqueue_failed(self, current, error: Exception) -> None:
        self.enqueue_failures.append((current, error))


def test_post_queues_new_analysis_and_returns_202(monkeypatch) -> None:
    current = analysis(AnalysisStatus.PENDING)
    service = FakeService(current, should_enqueue=True)
    queued = []
    monkeypatch.setattr(endpoint_module.analyze_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == 202
    assert response.json() == {
        "video_id": "video-1",
        "status": "pending",
        "completed_at": None,
        "error_message": None,
    }
    assert queued == ["video-1"]


def test_post_completed_analysis_returns_full_result_without_enqueue(monkeypatch) -> None:
    timestamp = datetime.now(UTC)
    current = analysis(
        AnalysisStatus.COMPLETED,
        summary="Summary",
        topics=[{"name": "Editing", "description": None, "relevance": 1.0}],
        keywords=["editing"],
        sentiment="positive",
        hook_candidates=[
            {
                "text": "Hook",
                "start_time": 0.0,
                "end_time": 3.0,
                "reason": None,
                "score": 0.6,
            }
        ],
        clip_candidates=[
            {
                "title": "Clip",
                "start_time": 0.0,
                "end_time": 20.0,
                "reason": None,
                "score": 0.5,
            }
        ],
        completed_at=timestamp,
    )
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.analyze_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == 200
    assert response.json()["summary"] == "Summary"
    assert response.json()["topics"][0]["name"] == "Editing"
    assert response.json()["hook_candidates"][0]["text"] == "Hook"
    assert queued == []


def test_post_processing_analysis_does_not_enqueue(monkeypatch) -> None:
    current = analysis(AnalysisStatus.PROCESSING)
    service = FakeService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.analyze_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert service.request_calls == 1
    assert queued == []


def test_post_failed_analysis_reenqueues_same_row(monkeypatch) -> None:
    current = analysis(AnalysisStatus.FAILED, error_message="Previous failure")

    class RetryService(FakeService):
        async def request_analysis(self, video_id: str):
            self.current.status = AnalysisStatus.PENDING
            self.current.error_message = None
            return self.current, True

    service = RetryService(current)
    queued = []
    monkeypatch.setattr(endpoint_module.analyze_video, "delay", queued.append)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert service.current is current
    assert queued == ["video-1"]


def test_get_completed_analysis_returns_structured_result() -> None:
    current = analysis(
        AnalysisStatus.COMPLETED,
        summary="Summary",
        topics=[{"name": "Topic", "description": "Details", "relevance": 0.9}],
        keywords=["topic"],
        sentiment="neutral",
        hook_candidates=[
            {
                "text": "Hook",
                "start_time": 0.0,
                "end_time": 2.0,
                "reason": "Opening",
                "score": 0.6,
            }
        ],
        clip_candidates=[
            {
                "title": "Clip",
                "start_time": 0.0,
                "end_time": 15.0,
                "reason": "Window",
                "score": 0.5,
            }
        ],
        completed_at=datetime.now(UTC),
    )

    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/videos/video-1/analysis")

    body = response.json()
    assert response.status_code == 200
    assert body["summary"] == "Summary"
    assert body["keywords"] == ["topic"]
    assert body["clip_candidates"][0]["title"] == "Clip"
    assert body["completed_at"] is not None


@pytest.mark.parametrize(
    ("state", "error_message"),
    [
        (AnalysisStatus.PENDING, None),
        (AnalysisStatus.PROCESSING, None),
        (AnalysisStatus.FAILED, "Analyzer failed"),
    ],
)
def test_get_incomplete_analysis_returns_status(state, error_message) -> None:
    current = analysis(state, error_message=error_message)

    with client_for(FakeService(current)) as client:
        response = client.get("/api/v1/videos/video-1/analysis")

    assert response.status_code == 200
    assert response.json() == {
        "video_id": "video-1",
        "status": state.value,
        "completed_at": None,
        "error_message": error_message,
    }


def test_get_missing_analysis_returns_404() -> None:
    class MissingService(FakeService):
        async def get_analysis(self, video_id: str):
            raise AnalysisNotFoundError

    with client_for(MissingService(None)) as client:
        response = client.get("/api/v1/videos/video-1/analysis")

    assert response.status_code == 404
    assert response.json() == {"detail": "Video analysis not found"}


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(VideoNotFoundError(), 404), (TranscriptNotReadyError(), 409)],
)
def test_post_maps_video_precondition_errors(error, expected_status: int) -> None:
    class FailingService(FakeService):
        async def request_analysis(self, video_id: str):
            raise error

    with client_for(FailingService(None)) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == expected_status


def test_enqueue_failure_persists_failure_and_returns_503(monkeypatch) -> None:
    current = analysis(AnalysisStatus.PENDING)
    service = FakeService(current, should_enqueue=True)

    def fail_enqueue(video_id: str) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(endpoint_module.analyze_video, "delay", fail_enqueue)

    with client_for(service) as client:
        response = client.post("/api/v1/videos/video-1/analysis")

    assert response.status_code == 503
    assert response.json() == {"detail": "Video analysis could not be queued"}
    assert len(service.enqueue_failures) == 1
    assert service.enqueue_failures[0][0] is current
    assert str(service.enqueue_failures[0][1]) == "broker unavailable"
