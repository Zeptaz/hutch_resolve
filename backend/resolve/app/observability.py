"""Safe, low-cardinality HTTP request observability for Resolve.

This module intentionally never inspects request or response bodies, headers,
query parameters, exception messages, or provider payloads. Integrators may set
``request.state.error_code`` in an error handler to include a stable public
error code in the event.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from starlette.responses import Response

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_REQUEST_ID_HEADER = "X-Request-Id"


def request_id_for(request: Request) -> str:
    """Return a validated request ID, creating one when the supplied value is invalid."""
    existing = getattr(request.state, "request_id", None)
    if existing:
        try:
            return str(UUID(str(existing)))
        except (TypeError, ValueError, AttributeError):
            pass
    try:
        request_id = str(UUID(request.headers.get(_REQUEST_ID_HEADER, "")))
    except (TypeError, ValueError, AttributeError):
        request_id = str(uuid4())
    request.state.request_id = request_id
    return request_id


def build_request_event(
    request: Request,
    *,
    status_code: int,
    elapsed_ms: float,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Build the fixed safe event shape; use the route template, never raw URL."""
    route = request.scope.get("route")
    route_template = getattr(route, "path", None)
    if not isinstance(route_template, str) or not route_template.startswith("/"):
        route_template = "unmatched"

    if error_code is None:
        error_code = getattr(request.state, "error_code", None)
    if error_code is not None and not _ERROR_CODE.fullmatch(str(error_code)):
        error_code = "UNCLASSIFIED"

    event: dict[str, Any] = {
        "event": "http_request",
        "request_id": request_id_for(request),
        "method": request.method,
        "route": route_template,
        "status_code": int(status_code),
        "elapsed_ms": round(max(0.0, float(elapsed_ms)), 3),
    }
    if status_code >= 400:
        event["error_code"] = error_code or "HTTP_ERROR"
    return event


def create_request_middleware(
    event_logger: logging.Logger | None = None,
) -> Callable[[Request, Callable[[Request], Any]], Any]:
    """Create FastAPI-compatible middleware that logs one safe JSON event/request.

    It sets and returns ``X-Request-Id``. Unhandled exceptions are logged with a
    fixed ``UNHANDLED_ERROR`` code and re-raised without logging exception text.
    """
    target_logger = event_logger or logging.getLogger("hutch_resolve.http")

    async def middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
        request_id = request_id_for(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            event = build_request_event(
                request,
                status_code=500,
                elapsed_ms=elapsed_ms,
                error_code="UNHANDLED_ERROR",
            )
            target_logger.error(json.dumps(event, separators=(",", ":"), sort_keys=True))
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        event = build_request_event(
            request,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        response.headers[_REQUEST_ID_HEADER] = request_id
        target_logger.info(json.dumps(event, separators=(",", ":"), sort_keys=True))
        return response

    return middleware
