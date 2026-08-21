class AppError(Exception):
    """Base application exception safe to expose through the API boundary."""

    status_code = 400
    detail = "Application error"


class ResourceNotFoundError(AppError):
    status_code = 404
    detail = "Resource not found"


class VideoNotFoundError(ResourceNotFoundError):
    detail = "Video not found"


class TranscriptNotReadyError(AppError):
    status_code = 409
    detail = "Video transcript is not ready"


class AnalysisNotFoundError(ResourceNotFoundError):
    detail = "Video analysis not found"


class AnalysisProcessingError(AppError):
    status_code = 422
    detail = "Video analysis processing failed"


class AnalysisEnqueueError(AppError):
    status_code = 503
    detail = "Video analysis could not be queued"


class CompletedAnalysisNotFoundError(AppError):
    status_code = 409
    detail = "Completed video analysis is required for script generation"


class ScriptNotFoundError(ResourceNotFoundError):
    detail = "Script not found"


class InvalidHookCandidateIndexError(AppError):
    status_code = 422
    detail = "Preferred hook candidate index is out of range"


class InvalidClipCandidateIndexError(AppError):
    status_code = 422
    detail = "Preferred clip candidate index is out of range"


class ScriptGenerationError(AppError):
    status_code = 422
    detail = "Script generation failed"


class ScriptEnqueueError(AppError):
    status_code = 503
    detail = "Script generation could not be queued"


class ScriptNotReadyError(AppError):
    status_code = 409
    detail = "Completed script is required for voice generation"


class UnusableScriptContentError(AppError):
    status_code = 422
    detail = "Script content is not usable for voice generation"


class VoiceTrackNotFoundError(ResourceNotFoundError):
    detail = "Voice track not found"


class VoiceGenerationError(AppError):
    status_code = 422
    detail = "Voice generation failed"


class VoiceEnqueueError(AppError):
    status_code = 503
    detail = "Voice generation could not be queued"


class BrollScriptNotReadyError(AppError):
    status_code = 409
    detail = "Completed script is required for B-roll retrieval"


class BrollUnusableScriptError(AppError):
    status_code = 422
    detail = "Script content is not usable for B-roll retrieval"


class BrollCollectionNotFoundError(ResourceNotFoundError):
    detail = "B-roll collection not found"


class BrollAssetNotFoundError(ResourceNotFoundError):
    detail = "B-roll asset not found"


class UnsupportedBrollQueryStrategyError(AppError):
    status_code = 422
    detail = "Unsupported B-roll query strategy"


class BrollNoResultsError(AppError):
    status_code = 422
    detail = "No valid B-roll candidates were returned"


class BrollRetrievalError(AppError):
    status_code = 422
    detail = "B-roll retrieval failed"


class BrollEnqueueError(AppError):
    status_code = 503
    detail = "B-roll retrieval could not be queued"


class RenderScriptNotReadyError(AppError):
    status_code = 409
    detail = "Completed script is required for video rendering"


class RenderVoiceTrackNotReadyError(AppError):
    status_code = 409
    detail = "Completed voice track is required for video rendering"


class RenderVoiceTrackMismatchError(AppError):
    status_code = 422
    detail = "Voice track does not belong to the render script"


class RenderBrollCollectionMismatchError(AppError):
    status_code = 422
    detail = "B-roll collection does not belong to the render script"


class RenderBrollCollectionNotReadyError(AppError):
    status_code = 409
    detail = "Completed B-roll collection is required for video rendering"


class VideoRenderNotFoundError(ResourceNotFoundError):
    detail = "Video render not found"


class UnusableVideoRenderInputError(AppError):
    status_code = 422
    detail = "Video render input is not usable"


class VideoRenderingError(AppError):
    status_code = 422
    detail = "Video rendering failed"


class VideoRenderEnqueueError(AppError):
    status_code = 503
    detail = "Video rendering could not be queued"


class RenderNotReadyForPublishingError(AppError):
    status_code = 409
    detail = "Completed video render is required for publishing"


class UnusablePublishArtifactError(AppError):
    status_code = 422
    detail = "Video render artifact is not usable for publishing"


class PublishJobNotFoundError(ResourceNotFoundError):
    detail = "Publish job not found"


class PublishJobCancelledError(AppError):
    status_code = 409
    detail = "Cancelled publish job cannot be processed"


class PublishNotDueError(AppError):
    status_code = 409
    detail = "Scheduled publish job is not due"


class PublishCancellationConflictError(AppError):
    status_code = 409
    detail = "Publish job cannot be cancelled in its current state"


class PublishingError(AppError):
    status_code = 422
    detail = "Publishing failed"

    def __init__(self, message: str = "Publishing failed") -> None:
        super().__init__(message)


class PublishingTransientError(PublishingError):
    detail = "Publishing provider is temporarily unavailable"

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        super().__init__("Publishing provider is temporarily unavailable")
        self.retry_after_seconds = retry_after_seconds


class PublishingExecutionLockUnavailableError(PublishingError):
    status_code = 503
    detail = "Publishing execution is temporarily busy"

    def __init__(self) -> None:
        super().__init__("Publishing execution is temporarily busy")


class PublishingRateLimitError(PublishingTransientError):
    status_code = 429
    detail = "Publishing provider rate limit exceeded"

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        PublishingError.__init__(self, "Publishing provider rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class PublishingQuotaExceededError(PublishingError):
    status_code = 429
    detail = "Publishing provider quota exhausted"

    def __init__(self) -> None:
        super().__init__("Publishing provider quota exhausted")


class PublishingAuthenticationError(PublishingError):
    status_code = 409
    detail = "Publishing account must be reconnected"

    def __init__(self) -> None:
        super().__init__("Publishing account must be reconnected")


class PublishingPermanentError(PublishingError):
    detail = "Publishing request was rejected"

    def __init__(self) -> None:
        super().__init__("Publishing request was rejected")


class PublishEnqueueError(AppError):
    status_code = 503
    detail = "Publishing could not be queued"


class PublishingAccountNotFoundError(ResourceNotFoundError):
    detail = "Publishing account not found"


class PublishingAccountInactiveError(AppError):
    status_code = 409
    detail = "Publishing account is inactive"


class UnsupportedOAuthPublishingPlatformError(AppError):
    status_code = 409
    detail = "Publishing account platform does not support OAuth authorization"


class OAuthAuthorizationConfigurationError(AppError):
    status_code = 503
    detail = "OAuth authorization is not configured"


class OAuthAuthorizationUnavailableError(AppError):
    status_code = 503
    detail = "OAuth authorization is temporarily unavailable"


class OAuthCallbackValidationError(AppError):
    detail = "OAuth callback parameters are invalid"


class OAuthInvalidStateError(AppError):
    detail = "OAuth state is invalid, expired, or already used"


class OAuthConsentDeniedError(AppError):
    detail = "OAuth authorization was denied"


class OAuthCallbackConfigurationError(AppError):
    status_code = 503
    detail = "OAuth callback is not configured"


class OAuthTokenExchangeFailedError(AppError):
    status_code = 502
    detail = "OAuth token exchange failed"


class OAuthCredentialPersistenceError(AppError):
    status_code = 500
    detail = "OAuth credential persistence failed"


class OAuthCredentialUnavailableError(AppError):
    status_code = 409
    detail = "OAuth credential is unavailable"


class OAuthCredentialRefreshUnavailableError(AppError):
    status_code = 409
    detail = "OAuth credential must be reconnected"


class OAuthCredentialRefreshFailedError(AppError):
    status_code = 502
    detail = "OAuth credential refresh failed"


class ProviderError(AppError):
    status_code = 502
    detail = "External provider error"


class UnsupportedMediaError(AppError):
    status_code = 415
    detail = "Unsupported video format"


class UploadTooLargeError(AppError):
    status_code = 413
    detail = "Uploaded file exceeds the configured size limit"


class MediaProcessingError(AppError):
    status_code = 422
    detail = "Video processing failed"


class StorageError(AppError):
    status_code = 500
    detail = "Video storage operation failed"
