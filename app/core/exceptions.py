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
