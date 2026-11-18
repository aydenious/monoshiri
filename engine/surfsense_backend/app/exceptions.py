"""Structured error hierarchy for SurfSense.

Every error response follows a backward-compatible contract:

    {
      "error": {
        "code": "SOME_ERROR_CODE",
        "message": "Human-readable, client-safe message.",
        "status": 422,
        "request_id": "req_...",
        "timestamp": "2026-04-14T12:00:00Z",
        "report_url": null  # no upstream issue tracker in this fork
      },
      "detail": "Human-readable, client-safe message."   # legacy compat
    }
"""

from __future__ import annotations

GENERIC_5XX_MESSAGE = (
    "An internal error occurred. Please try again or report this issue if it persists."
)


class SurfSenseError(Exception):
    """Base exception that global handlers translate into the structured envelope."""

    def __init__(
        self,
        message: str = GENERIC_5XX_MESSAGE,
        *,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        safe_for_client: bool = True,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.safe_for_client = safe_for_client


class NotFoundError(SurfSenseError):
    def __init__(
        self,
        message: str = "The requested resource was not found.",
        *,
        code: str = "NOT_FOUND",
    ) -> None:
        super().__init__(message, code=code, status_code=404)


class ValidationError(SurfSenseError):
    def __init__(
        self, message: str = "Validation failed.", *, code: str = "VALIDATION_ERROR"
    ) -> None:
        super().__init__(message, code=code, status_code=422)
