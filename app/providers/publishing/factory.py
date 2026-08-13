from app.providers.publishing.base import PublishingProvider
from app.providers.publishing.local import LocalPublishingProvider


def create_publishing_provider() -> PublishingProvider:
    return LocalPublishingProvider()
