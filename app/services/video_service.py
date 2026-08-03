from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError, UnsupportedMediaError
from app.models.video import Video
from app.repositories.video_repository import VideoRepository
from app.schemas.video import VideoMetadata
from app.services.ffmpeg_service import FFmpegService
from app.services.storage_service import StorageService
from app.services.whisper_service import WhisperService


class VideoService:
    supported_extensions = {".mp4", ".mov", ".mkv", ".webm"}

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageService,
        ffmpeg: FFmpegService,
        whisper: WhisperService,
    ) -> None:
        self.session = session
        self.repository = VideoRepository(session)
        self.storage = storage
        self.ffmpeg = ffmpeg
        self.whisper = whisper

    async def upload(self, upload: UploadFile) -> Video:
        filename = upload.filename or "upload"
        extension = Path(filename).suffix.lower()
        if extension not in self.supported_extensions:
            raise UnsupportedMediaError
        video_id = str(uuid4())
        await self.storage.save_upload(video_id, extension, upload)
        try:
            video = await self.repository.create(
                Video(id=video_id, filename=filename, status="uploaded")
            )
            await self.session.commit()
            return video
        except Exception:
            await self.session.rollback()
            self.storage.remove_video(video_id)
            raise

    async def process(self, video_id: str) -> None:
        video = await self.repository.get(video_id)
        if video is None:
            raise ResourceNotFoundError
        try:
            await self.repository.update_status(video, "processing")
            await self.session.commit()
            original_path = self.storage.original_path(video_id, Path(video.filename).suffix)
            metadata = self.ffmpeg.extract_metadata(original_path)
            video.duration = metadata.duration
            video.width = metadata.width
            video.height = metadata.height
            video.fps = metadata.fps
            video.metadata_data = metadata.model_dump()
            await self.session.commit()
            audio_path = self.ffmpeg.extract_audio(original_path, self.storage.audio_path(video_id))
            transcript = self.whisper.transcribe(audio_path)
            self.storage.save_json(self.storage.transcript_path(video_id), transcript)
            await self.repository.upsert_transcript(
                video, transcript["language"], transcript["transcript"]
            )
            await self.repository.update_status(video, "completed")
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            video = await self.repository.get(video_id)
            if video is not None:
                await self.repository.update_status(video, "failed")
                await self.session.commit()
            raise

    async def status(self, video_id: str) -> tuple[Video, VideoMetadata | None]:
        video = await self.repository.get(video_id)
        if video is None:
            raise ResourceNotFoundError
        metadata = (
            VideoMetadata.model_validate(video.metadata_data) if video.metadata_data else None
        )
        return video, metadata
