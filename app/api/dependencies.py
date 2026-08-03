from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.services.ffmpeg_service import FFmpegService
from app.services.storage_service import StorageService
from app.services.video_service import VideoService
from app.services.whisper_service import WhisperService

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_video_service(session: DatabaseSession) -> VideoService:
    settings = get_settings()
    return VideoService(
        session=session,
        storage=StorageService(settings.storage_root, settings.max_upload_size_mb * 1024 * 1024),
        ffmpeg=FFmpegService(),
        whisper=WhisperService(
            settings.whisper_model, settings.whisper_device, settings.whisper_compute_type
        ),
    )


VideoServiceDependency = Annotated[VideoService, Depends(get_video_service)]
