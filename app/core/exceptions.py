class AppError(Exception):
    """Base application exception safe to expose through the API boundary."""

    status_code = 400
    detail = "Application error"


class ResourceNotFoundError(AppError):
    status_code = 404
    detail = "Resource not found"


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
