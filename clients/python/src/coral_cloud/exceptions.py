"""Exception hierarchy for the Coral Cloud SDK.

All errors raised by :class:`coral_cloud.CoralCloud` derive from
:class:`CoralError`, so callers can catch broadly or narrowly.
"""

from __future__ import annotations


class CoralError(Exception):
    """Base class for every error raised by the SDK."""


class CoralConfigError(CoralError):
    """The client was constructed with invalid/missing configuration.

    e.g. no API key supplied and ``CORAL_API_KEY`` is unset.
    """


class CoralAPIError(CoralError):
    """The server returned an error response.

    Attributes
    ----------
    status_code:
        HTTP status code.
    code:
        Machine-readable ``error`` field from the response body, if any.
    message:
        Human-readable ``message`` field from the response body, if any.
    """

    def __init__(
        self,
        status_code: int,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        detail = message or code or "request failed"
        super().__init__(f"[{status_code}] {detail}")


class CoralAuthError(CoralAPIError):
    """401 — missing or invalid API key."""


class CoralNotFoundError(CoralAPIError):
    """404 — resource not found in this project."""


class CoralConflictError(CoralAPIError):
    """409 — resource not in a valid state for the operation."""


class CoralRateLimitError(CoralAPIError):
    """429 — quota or rate limit exceeded."""


def error_for_status(
    status_code: int,
    *,
    code: str | None = None,
    message: str | None = None,
) -> CoralAPIError:
    """Map an HTTP status code to the most specific :class:`CoralAPIError`."""
    cls = {
        401: CoralAuthError,
        404: CoralNotFoundError,
        409: CoralConflictError,
        429: CoralRateLimitError,
    }.get(status_code, CoralAPIError)
    return cls(status_code, code=code, message=message)
