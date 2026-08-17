from fastapi import APIRouter

from app.api.v1.endpoints.broll import router as broll_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.oauth_authorization import router as oauth_authorization_router
from app.api.v1.endpoints.oauth_callback import router as oauth_callback_router
from app.api.v1.endpoints.publishing import router as publishing_router
from app.api.v1.endpoints.scripts import router as scripts_router
from app.api.v1.endpoints.video_analysis import router as video_analysis_router
from app.api.v1.endpoints.video_renders import router as video_renders_router
from app.api.v1.endpoints.videos import router as videos_router
from app.api.v1.endpoints.voice_tracks import router as voice_tracks_router

router = APIRouter()
router.include_router(broll_router, tags=["broll"])
router.include_router(health_router, tags=["health"])
router.include_router(oauth_authorization_router, tags=["oauth"])
router.include_router(oauth_callback_router, tags=["oauth"])
router.include_router(publishing_router, tags=["publishing"])
router.include_router(videos_router, tags=["videos"])
router.include_router(video_analysis_router, tags=["video-analysis"])
router.include_router(scripts_router, tags=["scripts"])
router.include_router(voice_tracks_router, tags=["voice-tracks"])
router.include_router(video_renders_router, tags=["video-renders"])
