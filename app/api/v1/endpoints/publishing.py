from fastapi import APIRouter, Response, status

from app.api.dependencies import PublishingServiceDependency
from app.core.exceptions import PublishEnqueueError
from app.models.publish_job import PublishJob, PublishStatus
from app.schemas.publish_job import (
    PublishJobResponse,
    PublishJobStatusResponse,
    PublishRequest,
)
from app.tasks.publishing import execute_publish

router = APIRouter()

PublishJobAPIResponse = PublishJobResponse | PublishJobStatusResponse


def _response_for(publish_job: PublishJob) -> PublishJobAPIResponse:
    if publish_job.status == PublishStatus.PUBLISHED:
        return PublishJobResponse.model_validate(publish_job)
    return PublishJobStatusResponse.model_validate(publish_job)


async def _enqueue(publish_job: PublishJob, service: PublishingServiceDependency) -> None:
    try:
        execute_publish.delay(publish_job.id)
    except Exception as error:
        await service.mark_publish_enqueue_failed(publish_job, error)
        raise PublishEnqueueError from error


@router.post(
    "/renders/{video_render_id}/publish-jobs",
    response_model=PublishJobAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_publish_job(
    video_render_id: int,
    request: PublishRequest,
    service: PublishingServiceDependency,
) -> PublishJobAPIResponse:
    publish_job, should_enqueue = await service.request_publish_job(video_render_id, request)
    if should_enqueue:
        await _enqueue(publish_job, service)
    return _response_for(publish_job)


@router.get(
    "/renders/{video_render_id}/publish-jobs",
    response_model=list[PublishJobAPIResponse],
)
async def list_publish_jobs(
    video_render_id: int, service: PublishingServiceDependency
) -> list[PublishJobAPIResponse]:
    jobs = await service.list_publish_jobs_for_render(video_render_id)
    return [_response_for(job) for job in jobs]


@router.get("/publish-jobs/{publish_job_id}", response_model=PublishJobAPIResponse)
async def get_publish_job(
    publish_job_id: int, service: PublishingServiceDependency
) -> PublishJobAPIResponse:
    return _response_for(await service.get_publish_job(publish_job_id))


@router.post("/publish-jobs/{publish_job_id}/retry", response_model=PublishJobAPIResponse)
async def retry_publish_job(
    publish_job_id: int,
    response: Response,
    service: PublishingServiceDependency,
) -> PublishJobAPIResponse:
    publish_job, should_enqueue = await service.prepare_publish_retry(publish_job_id)
    if publish_job.status in {PublishStatus.PUBLISHED, PublishStatus.PUBLISHING}:
        return _response_for(publish_job)
    if should_enqueue:
        await _enqueue(publish_job, service)
    response.status_code = status.HTTP_202_ACCEPTED
    return _response_for(publish_job)


@router.post(
    "/publish-jobs/{publish_job_id}/cancel",
    response_model=PublishJobStatusResponse,
)
async def cancel_publish_job(
    publish_job_id: int, service: PublishingServiceDependency
) -> PublishJobStatusResponse:
    publish_job = await service.cancel_publish_job_and_commit(publish_job_id)
    return PublishJobStatusResponse.model_validate(publish_job)
