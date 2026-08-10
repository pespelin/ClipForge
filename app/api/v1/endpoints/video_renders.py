from fastapi import APIRouter, Response, status

from app.api.dependencies import VideoRenderServiceDependency
from app.core.exceptions import VideoRenderEnqueueError
from app.models.video_render import VideoRender, VideoRenderStatus
from app.schemas.video_render import (
    VideoRenderRequest,
    VideoRenderResponse,
    VideoRenderStatusResponse,
)
from app.tasks.video_rendering import render_video

router = APIRouter()

VideoRenderAPIResponse = VideoRenderResponse | VideoRenderStatusResponse


def _response_for(video_render: VideoRender) -> VideoRenderAPIResponse:
    if video_render.status == VideoRenderStatus.COMPLETED:
        return VideoRenderResponse.model_validate(video_render)
    return VideoRenderStatusResponse.model_validate(video_render)


async def _enqueue(video_render: VideoRender, service: VideoRenderServiceDependency) -> None:
    try:
        render_video.delay(video_render.id)
    except Exception as error:
        await service.mark_render_enqueue_failed(video_render, error)
        raise VideoRenderEnqueueError from error


@router.post(
    "/scripts/{script_id}/renders",
    response_model=VideoRenderAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_render(
    script_id: int,
    request: VideoRenderRequest,
    service: VideoRenderServiceDependency,
) -> VideoRenderAPIResponse:
    video_render = await service.request_video_render(
        script_id,
        request.voice_track_id,
        request.broll_collection_id,
        request.options,
    )
    await _enqueue(video_render, service)
    return _response_for(video_render)


@router.get(
    "/scripts/{script_id}/renders",
    response_model=list[VideoRenderAPIResponse],
)
async def list_renders(
    script_id: int, service: VideoRenderServiceDependency
) -> list[VideoRenderAPIResponse]:
    video_renders = await service.list_renders_for_script(script_id)
    return [_response_for(video_render) for video_render in video_renders]


@router.get("/renders/{render_id}", response_model=VideoRenderAPIResponse)
async def get_render(
    render_id: int, service: VideoRenderServiceDependency
) -> VideoRenderAPIResponse:
    return _response_for(await service.get_render(render_id))


@router.post("/renders/{render_id}/retry", response_model=VideoRenderAPIResponse)
async def retry_render(
    render_id: int,
    response: Response,
    service: VideoRenderServiceDependency,
) -> VideoRenderAPIResponse:
    video_render, should_enqueue = await service.prepare_render_retry(render_id)
    if not should_enqueue:
        return _response_for(video_render)

    await _enqueue(video_render, service)
    response.status_code = status.HTTP_202_ACCEPTED
    return _response_for(video_render)
