from pydantic import BaseModel


class OAuthCallbackResponse(BaseModel):
    publishing_account_id: int
    connected: bool
