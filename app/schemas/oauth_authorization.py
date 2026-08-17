from pydantic import BaseModel, HttpUrl


class OAuthAuthorizationResponse(BaseModel):
    authorization_url: HttpUrl
