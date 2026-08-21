from app.models.publish_job import PublishPlatform
from app.providers.publishing.dependencies import PublishingAccessCredential
from app.repositories.publishing_account_repository import PublishingAccountRepository
from app.services.oauth_credential_resolver import OAuthCredentialResolver


class PublishingCredentialResolutionError(Exception):
    """Raised when an account reference cannot yield a publishing credential."""

    def __init__(self) -> None:
        super().__init__("Publishing credential could not be resolved")


class OAuthPublishingCredentialResolver:
    """Bridge an opaque remote account reference to the OAuth credential lifecycle."""

    def __init__(
        self,
        account_repository: PublishingAccountRepository,
        credential_resolver: OAuthCredentialResolver,
    ) -> None:
        self._account_repository = account_repository
        self._credential_resolver = credential_resolver

    async def resolve(self, account_reference: str) -> PublishingAccessCredential:
        try:
            account = await self._account_repository.get_by_platform_and_remote_account_id(
                PublishPlatform.YOUTUBE,
                account_reference,
            )
            if (
                account is None
                or not account.is_active
                or account.platform != PublishPlatform.YOUTUBE
            ):
                raise PublishingCredentialResolutionError
            return await self._credential_resolver.resolve(account.id)
        except PublishingCredentialResolutionError:
            raise
        except Exception:
            raise PublishingCredentialResolutionError from None
