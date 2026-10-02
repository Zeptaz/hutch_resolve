"""Model client boundary (T-02). Tests use fakes; production uses Gemini.

Model text is untrusted: callers validate it against a schema and never treat
it as evidence, a permission or an instruction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class ModelError(Exception):
    """Provider failure (auth, quota, network, safety block, empty response)."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelClient(Protocol):
    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> ModelReply: ...


class GeminiModelClient:
    """Gemini via the official google-genai SDK.

    The caller (Extractor) owns the total 6 s budget, so SDK retries are disabled
    and a per-request HTTP timeout is set as a backstop.
    """

    def __init__(self, api_key: str, model: str, request_timeout_ms: int = 6000) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=request_timeout_ms, retry_options=types.HttpRetryOptions(attempts=1)),
        )

    @classmethod
    def from_env(cls) -> "GeminiModelClient | None":
        """GEMINI_API_KEY + GEMINI_TEXT_MODEL (docs/contracts.md). None if not configured."""
        key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_TEXT_MODEL")
        if not key or not model:
            return None
        return cls(api_key=key, model=model)

    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> ModelReply:
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
            max_output_tokens=1024,
        )
        try:
            response = await self._client.aio.models.generate_content(model=self._model, contents=prompt, config=config)
        except Exception as err:  # SDK raises several error families; all mean "no usable output"
            raise ModelError(type(err).__name__) from err
        text = response.text
        if not text:
            raise ModelError("EMPTY_RESPONSE")
        usage = response.usage_metadata
        return ModelReply(
            text=text,
            provider="gemini",
            model=response.model_version or self._model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
