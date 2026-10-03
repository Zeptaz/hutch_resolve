from __future__ import annotations

import asyncio
import json
import uuid

import httpx

from .voice_security import canonical_json, signed_headers


class VoiceServiceError(RuntimeError):
    pass


class VoiceSessionClient:
    """Resolve-side client for provisioning short-lived browser Voice grants."""

    def __init__(self, base_url: str, secret: bytes, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=2.0), follow_redirects=False
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_session(self, payload: dict, *, event_id: str | None = None) -> dict:
        body = canonical_json(payload)
        event_id = event_id or str(uuid.uuid4())
        url = f"{self.base_url}/api/hutch/sessions"
        for attempt in range(2):
            try:
                response = await self._client.post(
                    url, content=body,
                    headers=signed_headers(self.secret, event_id, body),
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise VoiceServiceError("voice_service_unavailable") from exc
            if response.status_code >= 500 and attempt == 0:
                await asyncio.sleep(0.15)
                continue
            if response.status_code >= 400:
                raise VoiceServiceError("voice_session_rejected")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("response is not an object")
                return data
            except (ValueError, json.JSONDecodeError) as exc:
                raise VoiceServiceError("voice_invalid_response") from exc
        raise VoiceServiceError("voice_service_unavailable")
