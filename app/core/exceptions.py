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
