import pytest
from celery.exceptions import Retry

from app.tasks import video as task_module


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass


def test_video_task_enqueues_analysis_after_processing(monkeypatch) -> None:
    events = []

    class SuccessfulService:
        async def process(self, video_id: str) -> None:
            events.append(f"processed:{video_id}")

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(task_module, "get_video_service", lambda session: SuccessfulService())
    monkeypatch.setattr(
        task_module.analyze_video,
        "delay",
        lambda video_id: events.append(f"enqueued:{video_id}"),
    )

    task_module.process_video.run("video-1")

    assert events == ["processed:video-1", "enqueued:video-1"]


def test_video_task_does_not_enqueue_analysis_when_processing_fails(monkeypatch) -> None:
    enqueued = []

    class FailingService:
        async def process(self, video_id: str) -> None:
            raise RuntimeError("transcription failed")

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(task_module, "get_video_service", lambda session: FailingService())
    monkeypatch.setattr(task_module.analyze_video, "delay", enqueued.append)

    with pytest.raises((RuntimeError, Retry)):
        task_module.process_video.run("video-1")

    assert enqueued == []
