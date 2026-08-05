from fastapi import APIRouter, Response, status

from app.api.dependencies import VoiceGenerationServiceDependency
from app.core.exceptions import VoiceEnqueueError
from app.models.voice_track import VoiceTrack, VoiceTrackStatus
from app.schemas.voice_track import (
    VoiceGenerationOptions,
    VoiceTrackResponse,
    VoiceTrackStatusResponse,
)
from app.tasks.voice_generation import generate_voice

router = APIRouter()

VoiceTrackAPIResponse = VoiceTrackResponse | VoiceTrackStatusResponse


def _response_for(voice_track: VoiceTrack) -> VoiceTrackAPIResponse:
    if voice_track.status == VoiceTrackStatus.COMPLETED:
        return VoiceTrackResponse.model_validate(voice_track)
    return VoiceTrackStatusResponse.model_validate(voice_track)


async def _enqueue(voice_track: VoiceTrack, service: VoiceGenerationServiceDependency) -> None:
    try:
        generate_voice.delay(voice_track.id)
    except Exception as error:
        await service.mark_voice_enqueue_failed(voice_track, error)
        raise VoiceEnqueueError from error


@router.post(
    "/scripts/{script_id}/voice-tracks",
    response_model=VoiceTrackAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_voice_track(
    script_id: int,
    options: VoiceGenerationOptions,
    service: VoiceGenerationServiceDependency,
) -> VoiceTrackAPIResponse:
    voice_track = await service.request_voice_generation(script_id, options)
    await _enqueue(voice_track, service)
    return _response_for(voice_track)


@router.get(
    "/scripts/{script_id}/voice-tracks",
    response_model=list[VoiceTrackAPIResponse],
)
async def list_voice_tracks(
    script_id: int, service: VoiceGenerationServiceDependency
) -> list[VoiceTrackAPIResponse]:
    voice_tracks = await service.list_voice_tracks_for_script(script_id)
    return [_response_for(voice_track) for voice_track in voice_tracks]


@router.get("/voice-tracks/{voice_track_id}", response_model=VoiceTrackAPIResponse)
async def get_voice_track(
    voice_track_id: int, service: VoiceGenerationServiceDependency
) -> VoiceTrackAPIResponse:
    voice_track = await service.get_voice_track(voice_track_id)
    return _response_for(voice_track)


@router.post(
    "/voice-tracks/{voice_track_id}/retry",
    response_model=VoiceTrackAPIResponse,
)
async def retry_voice_track(
    voice_track_id: int,
    response: Response,
    service: VoiceGenerationServiceDependency,
) -> VoiceTrackAPIResponse:
    voice_track, should_enqueue = await service.prepare_voice_track_retry(voice_track_id)
    if not should_enqueue:
        return _response_for(voice_track)

    await _enqueue(voice_track, service)
    response.status_code = status.HTTP_202_ACCEPTED
    return _response_for(voice_track)
