from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.exceptions import UploadTooLargeError
from app.services.storage_service import StorageService


@pytest.mark.asyncio
async def test_storage_saves_upload(tmp_path) -> None:
    upload = UploadFile(filename="video.mp4", file=BytesIO(b"video"))
    storage = StorageService(tmp_path, max_upload_size_bytes=10)
    video_id = str(uuid4())

    path = await storage.save_upload(video_id, ".mp4", upload)

    assert path.read_bytes() == b"video"
    assert path == tmp_path / video_id / "original.mp4"


@pytest.mark.asyncio
async def test_storage_rejects_oversized_upload(tmp_path) -> None:
    upload = UploadFile(filename="video.mp4", file=BytesIO(b"too large"))
    storage = StorageService(tmp_path, max_upload_size_bytes=3)

    with pytest.raises(UploadTooLargeError):
        await storage.save_upload(str(uuid4()), ".mp4", upload)
