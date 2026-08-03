from fastapi import APIRouter, File, UploadFile, status

from app.api.dependencies import VideoServiceDependency
from app.schemas.video import UploadResponse, VideoStatusResponse
from app.tasks.video import process_video

router = APIRouter(prefix="/videos")


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_video(
    service: VideoServiceDependency, file: UploadFile = File(...)
) -> UploadResponse:
    video = await service.upload(file)
    process_video.delay(video.id)
    return UploadResponse(video_id=video.id, status=video.status)


@router.get("/{video_id}", response_model=VideoStatusResponse)
async def get_video(video_id: str, service: VideoServiceDependency) -> VideoStatusResponse:
    video, metadata = await service.status(video_id)
    return VideoStatusResponse(
        status=video.status,
        metadata=metadata,
        transcript_ready=video.transcript is not None,
        language=video.transcript.language if video.transcript else None,
        duration=video.duration,
    )
