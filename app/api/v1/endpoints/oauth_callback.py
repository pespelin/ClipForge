from fastapi import APIRouter

from app.api.dependencies import DatabaseSession, OAuthCallbackServiceDependency
from app.schemas.oauth_callback import OAuthCallbackResponse

router = APIRouter()


@router.get("/oauth/youtube/callback", response_model=OAuthCallbackResponse)
async def complete_youtube_oauth_callback(
    service: OAuthCallbackServiceDependency,
    session: DatabaseSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> OAuthCallbackResponse:
    try:
        result = await service.complete(
            state=state,
            authorization_code=code,
            provider_error=error,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return OAuthCallbackResponse(
        publishing_account_id=result.publishing_account_id,
        connected=result.connected,
    )
