"""Typed errors matching the contract error envelope (docs/contracts.md)."""

from __future__ import annotations

from typing import Any

# Contract error code -> HTTP status. Harry's API layer renders the envelope.
HTTP_STATUS: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "SESSION_EXPIRED": 401,
    "INVALID_SERVICE_SIGNATURE": 401,
    "ROLE_FORBIDDEN": 403,
    "CSRF_FAILED": 403,
    "ORIGIN_FORBIDDEN": 403,
    "RESOURCE_NOT_FOUND": 404,
    "STALE_VERSION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "PROPOSAL_INVALIDATED": 409,
    "TURN_IN_PROGRESS": 409,
    "CONVERSATION_BUSY": 409,
    "VALIDATION_ERROR": 422,
    "ACTION_NOT_ALLOWED": 422,
    "PROPOSAL_EXPIRED": 422,
    "CONFIRMATION_REQUIRED": 422,
    "RATE_LIMITED": 429,
    "DEPENDENCY_UNAVAILABLE": 503,
}

RETRYABLE_CODES = frozenset({"TURN_IN_PROGRESS", "RATE_LIMITED", "DEPENDENCY_UNAVAILABLE"})


class ResolveError(Exception):
    """Raised by ResolveFacade/repositories and by the conversation module itself."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in HTTP_STATUS:
            raise ValueError(f"unknown contract error code: {code}")
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message or code
        self.retryable = code in RETRYABLE_CODES if retryable is None else retryable
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]
