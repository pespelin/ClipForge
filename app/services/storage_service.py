import json
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from app.core.exceptions import StorageError, UploadTooLargeError


class StorageService:
    """Filesystem storage adapter. Replace this adapter to move media to S3."""

    def __init__(self, root: str | Path, max_upload_size_bytes: int) -> None:
        self.root = Path(root)
        self.max_upload_size_bytes = max_upload_size_bytes

    def video_dir(self, video_id: str) -> Path:
        UUID(video_id)  # refuse path traversal and invalid storage keys
        return self.root / video_id

    def original_path(self, video_id: str, extension: str) -> Path:
        return self.video_dir(video_id) / f"original{extension.lower()}"

    def audio_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "audio.wav"

    def transcript_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "transcript.json"

    async def save_upload(self, video_id: str, extension: str, upload: UploadFile) -> Path:
        target = self.original_path(video_id, extension)
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > self.max_upload_size_bytes:
                        output.close()
                        target.unlink(missing_ok=True)
                        raise UploadTooLargeError
                    output.write(chunk)
        except OSError as exc:
            raise StorageError from exc
        finally:
            await upload.close()
        return target

    def save_json(self, path: Path, data: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise StorageError from exc

    def file_size(self, path: Path) -> int:
        return path.stat().st_size

    def remove_video(self, video_id: str) -> None:
        shutil.rmtree(self.video_dir(video_id), ignore_errors=True)
