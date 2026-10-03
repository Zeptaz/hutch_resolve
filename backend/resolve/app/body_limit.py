"""Bound request bodies before FastAPI buffers them for JSON parsing."""

from __future__ import annotations

from uuid import UUID, uuid4

from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            request_id = str(UUID(headers.get(b"x-request-id", b"").decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            request_id = str(uuid4())

        def too_large():
            return JSONResponse(status_code=413, content={"error": {
                "code": "REQUEST_TOO_LARGE", "message": "Request body exceeds the allowed size",
                "retryable": False, "request_id": request_id, "details": {},
            }}, headers={"X-Request-Id": request_id})

        length = headers.get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                declared = self.max_bytes + 1
            if declared < 0 or declared > self.max_bytes:
                await too_large()(scope, receive, send)
                return

        chunks: list[bytes] = []
        received = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > self.max_bytes:
                await too_large()(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)
