import httpx
import pytest

from app.core.exceptions import (
    PublishingAuthenticationError,
    PublishingPermanentError,
    PublishingQuotaExceededError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.providers.publishing.youtube import YouTubePublishingProvider

RAW_SECRET = "clipforge-raw-google-message-secret-never-real"


def google_error(status: int, reason: str | None = None, **headers) -> httpx.Response:
    payload = {
        "error": {
            "code": status,
            "message": RAW_SECRET,
            "errors": ([{"reason": reason, "message": RAW_SECRET}] if reason else []),
        }
    }
    return httpx.Response(status, json=payload, headers=headers)


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_transient_statuses_are_retryable_and_secret_safe(status: int) -> None:
    with pytest.raises(PublishingTransientError) as error:
        YouTubePublishingProvider._raise_classified_error(google_error(status))

    assert not isinstance(error.value, PublishingRateLimitError)
    assert RAW_SECRET not in str(error.value)
    assert RAW_SECRET not in repr(error.value)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("120", 120), ("-1", None), ("abc", None), (None, None)],
)
def test_rate_limit_retry_after_integer_parsing_is_deterministic(
    header: str | None, expected: int | None
) -> None:
    headers = {"Retry-After": header} if header is not None else {}

    with pytest.raises(PublishingRateLimitError) as error:
        YouTubePublishingProvider._raise_classified_error(google_error(429, **headers))

    assert error.value.retry_after_seconds == expected


@pytest.mark.parametrize(
    ("status", "reason", "expected_error"),
    [
        (400, None, PublishingPermanentError),
        (401, None, PublishingAuthenticationError),
        (403, "quotaExceeded", PublishingQuotaExceededError),
        (403, "dailyLimitExceeded", PublishingQuotaExceededError),
        (403, "rateLimitExceeded", PublishingRateLimitError),
        (403, "userRateLimitExceeded", PublishingRateLimitError),
        (403, "authError", PublishingAuthenticationError),
        (403, "invalidCredentials", PublishingAuthenticationError),
        (403, "unknownReason", PublishingPermanentError),
    ],
)
def test_status_and_known_reason_classification(
    status: int, reason: str | None, expected_error: type[Exception]
) -> None:
    with pytest.raises(expected_error) as error:
        YouTubePublishingProvider._raise_classified_error(google_error(status, reason))

    assert RAW_SECRET not in str(error.value)
    assert RAW_SECRET not in repr(error.value)


def test_malformed_error_payload_uses_status_fallback() -> None:
    with pytest.raises(PublishingPermanentError):
        YouTubePublishingProvider._raise_classified_error(httpx.Response(403, content=b"not-json"))
