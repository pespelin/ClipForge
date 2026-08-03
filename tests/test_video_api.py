from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_video_service
from app.api.v1.endpoints.videos import router
from app.core.exception_handlers import register_exception_handlers
from app.tasks.video import process_video


def test_upload_endpoint_queues_processing(monkeypatch) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    queued = []

    class FakeService:
        async def upload(self, file):
            return SimpleNamespace(id="video-id", status="uploaded")

    app.dependency_overrides[get_video_service] = lambda: FakeService()
    monkeypatch.setattr(process_video, "delay", lambda video_id: queued.append(video_id))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/videos/upload",
            files={"file": ("demo.mp4", BytesIO(b"video"), "video/mp4")},
        )

    assert response.status_code == 202
    assert response.json() == {"video_id": "video-id", "status": "uploaded"}
    assert queued == ["video-id"]
