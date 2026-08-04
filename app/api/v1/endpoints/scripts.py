from fastapi import APIRouter, Response, status

from app.api.dependencies import ScriptGenerationServiceDependency
from app.core.exceptions import ScriptEnqueueError
from app.models.script import Script, ScriptStatus
from app.schemas.script import ScriptGenerationOptions, ScriptResponse, ScriptStatusResponse
from app.tasks.script_generation import generate_script

router = APIRouter()

ScriptAPIResponse = ScriptResponse | ScriptStatusResponse


def _response_for(script: Script) -> ScriptAPIResponse:
    if script.status == ScriptStatus.COMPLETED:
        return ScriptResponse.model_validate(script)
    return ScriptStatusResponse.model_validate(script)


async def _enqueue(script: Script, service: ScriptGenerationServiceDependency) -> None:
    try:
        generate_script.delay(script.id)
    except Exception as error:
        await service.mark_script_enqueue_failed(script, error)
        raise ScriptEnqueueError from error


@router.post(
    "/videos/{video_id}/scripts",
    response_model=ScriptAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_script(
    video_id: str,
    options: ScriptGenerationOptions,
    service: ScriptGenerationServiceDependency,
) -> ScriptAPIResponse:
    script = await service.request_script_generation(video_id, options)
    await _enqueue(script, service)
    return _response_for(script)


@router.get("/videos/{video_id}/scripts", response_model=list[ScriptAPIResponse])
async def list_scripts(
    video_id: str, service: ScriptGenerationServiceDependency
) -> list[ScriptAPIResponse]:
    scripts = await service.list_scripts_for_video(video_id)
    return [_response_for(script) for script in scripts]


@router.get("/scripts/{script_id}", response_model=ScriptAPIResponse)
async def get_script(
    script_id: int, service: ScriptGenerationServiceDependency
) -> ScriptAPIResponse:
    script = await service.get_script(script_id)
    return _response_for(script)


@router.post("/scripts/{script_id}/retry", response_model=ScriptAPIResponse)
async def retry_script(
    script_id: int,
    response: Response,
    service: ScriptGenerationServiceDependency,
) -> ScriptAPIResponse:
    script, should_enqueue = await service.prepare_script_retry(script_id)
    if not should_enqueue:
        return _response_for(script)

    await _enqueue(script, service)
    response.status_code = status.HTTP_202_ACCEPTED
    return _response_for(script)
