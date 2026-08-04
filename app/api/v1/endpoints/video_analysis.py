from fastapi import APIRouter, Response, status

from app.api.dependencies import VideoAnalysisServiceDependency
from app.core.exceptions import AnalysisEnqueueError
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.schemas.video_analysis import VideoAnalysisResponse, VideoAnalysisStatusResponse
from app.tasks.video_analysis import analyze_video

router = APIRouter(prefix="/videos")

AnalysisResponse = VideoAnalysisResponse | VideoAnalysisStatusResponse


def _response_for(analysis: VideoAnalysis) -> AnalysisResponse:
    if analysis.status == AnalysisStatus.COMPLETED:
        return VideoAnalysisResponse.model_validate(analysis)
    return VideoAnalysisStatusResponse.model_validate(analysis)


@router.post(
    "/{video_id}/analysis",
    response_model=AnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_video_analysis(
    video_id: str,
    response: Response,
    service: VideoAnalysisServiceDependency,
) -> AnalysisResponse:
    analysis, should_enqueue = await service.request_analysis(video_id)
    if not should_enqueue:
        response.status_code = status.HTTP_200_OK
        return _response_for(analysis)
    try:
        analyze_video.delay(video_id)
    except Exception as error:
        await service.mark_enqueue_failed(analysis, error)
        raise AnalysisEnqueueError from error
    return _response_for(analysis)


@router.get("/{video_id}/analysis", response_model=AnalysisResponse)
async def get_video_analysis(
    video_id: str, service: VideoAnalysisServiceDependency
) -> AnalysisResponse:
    analysis = await service.get_analysis(video_id)
    return _response_for(analysis)
