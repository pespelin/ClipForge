from types import SimpleNamespace

import pytest

from app.core.exceptions import AnalysisProcessingError, TranscriptNotReadyError
from app.models.video_analysis import AnalysisStatus
from app.tasks import video_analysis as task_module


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


async def test_analysis_task_composes_dependencies_and_returns_result(monkeypatch) -> None:
    session = FakeSession()
    dependencies = {}

    class FakeService:
        def __init__(self, video_repository, analysis_repository, analyzer) -> None:
            dependencies["video_repository"] = video_repository
            dependencies["analysis_repository"] = analysis_repository
            dependencies["analyzer"] = analyzer

        async def process_analysis(self, video_id: str):
            dependencies["video_id"] = video_id
            return SimpleNamespace(status=AnalysisStatus.COMPLETED)

    video_repository = object()
    analysis_repository = object()
    analyzer = object()
    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: video_repository)
    monkeypatch.setattr(
        task_module, "VideoAnalysisRepository", lambda received: analysis_repository
    )
    monkeypatch.setattr(task_module, "LocalVideoAnalyzer", lambda: analyzer)
    monkeypatch.setattr(task_module, "VideoAnalysisService", FakeService)

    result = await task_module._run_analysis("video-1")

    assert result == {"video_id": "video-1", "analysis_status": "completed"}
    assert dependencies == {
        "video_repository": video_repository,
        "analysis_repository": analysis_repository,
        "analyzer": analyzer,
        "video_id": "video-1",
    }
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_analysis_processing_failure_is_committed_and_reraised(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_analysis(self, video_id: str):
            raise AnalysisProcessingError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoAnalysisRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalVideoAnalyzer", object)
    monkeypatch.setattr(task_module, "VideoAnalysisService", FailingService)

    with pytest.raises(AnalysisProcessingError):
        await task_module._run_analysis("video-1")

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


async def test_precondition_failure_is_rolled_back_and_reraised(monkeypatch) -> None:
    session = FakeSession()

    class FailingService:
        def __init__(self, **dependencies) -> None:
            pass

        async def process_analysis(self, video_id: str):
            raise TranscriptNotReadyError

    monkeypatch.setattr(task_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(task_module, "VideoRepository", lambda received: object())
    monkeypatch.setattr(task_module, "VideoAnalysisRepository", lambda received: object())
    monkeypatch.setattr(task_module, "LocalVideoAnalyzer", object)
    monkeypatch.setattr(task_module, "VideoAnalysisService", FailingService)

    with pytest.raises(TranscriptNotReadyError):
        await task_module._run_analysis("video-1")

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


def test_sync_task_returns_async_result(monkeypatch) -> None:
    async def fake_run(video_id: str) -> dict[str, str]:
        return {"video_id": video_id, "analysis_status": "completed"}

    monkeypatch.setattr(task_module, "_run_analysis", fake_run)

    assert task_module.analyze_video.run("video-1") == {
        "video_id": "video-1",
        "analysis_status": "completed",
    }
