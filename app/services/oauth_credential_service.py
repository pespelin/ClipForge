from dataclasses import dataclass
from datetime import datetime

from app.models.oauth_credential import OAuthCredential
from app.repositories.oauth_credential_repository import OAuthCredentialRepository
from app.security import CredentialEncryptor


@dataclass(frozen=True, slots=True)
class OAuthCredentialInput:
    access_token: str
    refresh_token: str | None = None
    token_type: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OAuthCredentialData:
    publishing_account_id: int
    access_token: str
    refresh_token: str | None
    token_type: str | None
    scope: str | None
    expires_at: datetime | None


class OAuthCredentialService:
    def __init__(
        self,
        credential_repository: OAuthCredentialRepository,
        encryptor: CredentialEncryptor,
    ) -> None:
        self.credential_repository = credential_repository
        self.encryptor = encryptor

    async def store(
        self, publishing_account_id: int, credential_input: OAuthCredentialInput
    ) -> OAuthCredential:
        encrypted_access_token = self.encryptor.encrypt(credential_input.access_token)
        encrypted_refresh_token = (
            self.encryptor.encrypt(credential_input.refresh_token)
            if credential_input.refresh_token is not None
            else None
        )
        credential = await self.credential_repository.get_by_publishing_account_id(
            publishing_account_id
        )

        if credential is None:
            return await self.credential_repository.create(
                OAuthCredential(
                    publishing_account_id=publishing_account_id,
                    encrypted_access_token=encrypted_access_token,
                    encrypted_refresh_token=encrypted_refresh_token,
                    token_type=credential_input.token_type,
                    scope=credential_input.scope,
                    expires_at=credential_input.expires_at,
                )
            )

        credential.encrypted_access_token = encrypted_access_token
        if encrypted_refresh_token is not None:
            credential.encrypted_refresh_token = encrypted_refresh_token
        credential.token_type = credential_input.token_type
        credential.scope = credential_input.scope
        credential.expires_at = credential_input.expires_at
        return await self.credential_repository.save(credential)

    async def get_decrypted_by_publishing_account_id(
        self, publishing_account_id: int
    ) -> OAuthCredentialData | None:
        credential = await self.credential_repository.get_by_publishing_account_id(
            publishing_account_id
        )
        if credential is None:
            return None

        return OAuthCredentialData(
            publishing_account_id=credential.publishing_account_id,
            access_token=self.encryptor.decrypt(credential.encrypted_access_token),
            refresh_token=(
                self.encryptor.decrypt(credential.encrypted_refresh_token)
                if credential.encrypted_refresh_token is not None
                else None
            ),
            token_type=credential.token_type,
            scope=credential.scope,
            expires_at=credential.expires_at,
        )
