from app.core.exceptions import (
    PublishingAuthenticationError,
    PublishingError,
    PublishingPermanentError,
    PublishingQuotaExceededError,
    PublishingRateLimitError,
    PublishingTransientError,
)


def publishing_failure_category(error: BaseException) -> str:
    """Map publishing failures to a stable, secret-safe operational category."""
    if isinstance(error, PublishingRateLimitError):
        return "rate_limit"
    if isinstance(error, PublishingTransientError):
        return "transient"
    if isinstance(error, PublishingQuotaExceededError):
        return "quota"
    if isinstance(error, PublishingAuthenticationError):
        return "authentication"
    if isinstance(error, PublishingPermanentError):
        return "permanent"
    if isinstance(error, PublishingError):
        return "provider"
    return "provider"
