from fastapi import APIRouter

from app.api.dependencies import OAuthAuthorizationServiceDependency
from app.schemas.oauth_authorization import OAuthAuthorizationResponse

router = APIRouter()


@router.post(
    "/publishing-accounts/{publishing_account_id}/oauth/authorize",
    response_model=OAuthAuthorizationResponse,
)
async def authorize_publishing_account(
    publishing_account_id: int,
    service: OAuthAuthorizationServiceDependency,
) -> OAuthAuthorizationResponse:
    result = await service.authorize(publishing_account_id)
    return OAuthAuthorizationResponse(authorization_url=result.authorization_url)
