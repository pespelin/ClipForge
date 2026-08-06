from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.providers.analysis import LocalVideoAnalyzer
from app.providers.media import LocalMediaProvider
from app.providers.script import LocalScriptGenerator
from app.providers.tts import LocalTTSProvider
from app.repositories.broll_repository import BrollAssetRepository, BrollCollectionRepository
from app.repositories.script_repository import ScriptRepository
from app.repositories.video_analysis_repository import VideoAnalysisRepository
from app.repositories.video_repository import VideoRepository
from app.repositories.voice_track_repository import VoiceTrackRepository
from app.services.broll_retrieval_service import BrollRetrievalService
from app.services.ffmpeg_service import FFmpegService
from app.services.script_generation_service import ScriptGenerationService
from app.services.storage_service import StorageService
from app.services.video_analysis_service import VideoAnalysisService
from app.services.video_service import VideoService
from app.services.voice_generation_service import VoiceGenerationService
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


def get_video_analysis_service(session: DatabaseSession) -> VideoAnalysisService:
    return VideoAnalysisService(
        video_repository=VideoRepository(session),
        analysis_repository=VideoAnalysisRepository(session),
        analyzer=LocalVideoAnalyzer(),
    )


VideoAnalysisServiceDependency = Annotated[
    VideoAnalysisService, Depends(get_video_analysis_service)
]


def get_script_generation_service(session: DatabaseSession) -> ScriptGenerationService:
    return ScriptGenerationService(
        video_repository=VideoRepository(session),
        analysis_repository=VideoAnalysisRepository(session),
        script_repository=ScriptRepository(session),
        generator=LocalScriptGenerator(),
    )


ScriptGenerationServiceDependency = Annotated[
    ScriptGenerationService, Depends(get_script_generation_service)
]


def get_voice_generation_service(session: DatabaseSession) -> VoiceGenerationService:
    return VoiceGenerationService(
        script_repository=ScriptRepository(session),
        voice_track_repository=VoiceTrackRepository(session),
        tts_provider=LocalTTSProvider(get_settings().storage_root),
    )


VoiceGenerationServiceDependency = Annotated[
    VoiceGenerationService, Depends(get_voice_generation_service)
]


def get_broll_retrieval_service(session: DatabaseSession) -> BrollRetrievalService:
    return BrollRetrievalService(
        script_repository=ScriptRepository(session),
        collection_repository=BrollCollectionRepository(session),
        asset_repository=BrollAssetRepository(session),
        media_provider=LocalMediaProvider(),
    )


BrollRetrievalServiceDependency = Annotated[
    BrollRetrievalService, Depends(get_broll_retrieval_service)
]
