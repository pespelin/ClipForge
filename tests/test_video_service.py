from io import BytesIO

import pytest
from fastapi import UploadFile

from app.core.exceptions import UnsupportedMediaError
from app.services.ffmpeg_service import FFmpegService
from app.services.storage_service import StorageService
from app.services.video_service import VideoService
from app.services.whisper_service import WhisperService


class FakeSession:
    def __init__(self) -> None:
        self.items = []
        self.committed = False

    def add(self, item) -> None:
        self.items.append(item)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


@pytest.mark.asyncio
async def test_video_service_upload_persists_uploaded_video(tmp_path) -> None:
    session = FakeSession()
    service = VideoService(
        session,
        StorageService(tmp_path, 1024),
        FFmpegService(),
        WhisperService("base"),
    )

    video = await service.upload(UploadFile(filename="source.MP4", file=BytesIO(b"video")))

    assert video.status == "uploaded"
    assert session.committed
    assert (tmp_path / video.id / "original.mp4").exists()


@pytest.mark.asyncio
async def test_video_service_rejects_unknown_extensions(tmp_path) -> None:
    service = VideoService(
        FakeSession(), StorageService(tmp_path, 1024), FFmpegService(), WhisperService("base")
    )

    with pytest.raises(UnsupportedMediaError):
        await service.upload(UploadFile(filename="source.avi", file=BytesIO(b"video")))
