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
    provider: str
    model_name: str  # configured model; a reply may report a more specific version

    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> ModelReply: ...


class GeminiModelClient:
    """Gemini via the official google-genai SDK.

    The caller (Extractor) owns the total 6 s budget and cancels the call itself,
    so SDK retries are disabled. The HTTP timeout is only a backstop: the API
    rejects deadlines under 10 s ("Minimum allowed deadline is 10s").
    """

    MIN_DEADLINE_MS = 10_000

    QUOTA_COOLDOWN_SECONDS = 600.0  # after a 429, use the fallback model for this long before trying the main one again

    def __init__(self, api_key: str, model: str, request_timeout_ms: int = MIN_DEADLINE_MS, thinking_level: str | None = "LOW",
                 fallback_model: str | None = None, monotonic=None) -> None:
        import time

        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        # Used only when the main model's quota is exhausted (HTTP 429), so one empty quota is not an outage.
        self._fallback = fallback_model if fallback_model and fallback_model != model else None
        self._monotonic = monotonic or time.monotonic
        self._main_blocked_until = 0.0
        self.provider = "gemini"
        self.model_name = model
        # Newer Gemini models think by default; keep it low so extraction fits the 6 s budget.
        self._thinking = types.ThinkingConfig(thinking_level=thinking_level) if thinking_level else None
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(request_timeout_ms, self.MIN_DEADLINE_MS), retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    @classmethod
    def from_env(cls) -> "GeminiModelClient | None":
        """GEMINI_API_KEY + GEMINI_TEXT_MODEL (docs/contracts.md), optional GEMINI_THINKING_LEVEL
        (MINIMAL/LOW/MEDIUM/HIGH; empty disables the setting) and GEMINI_FALLBACK_MODEL (used only on quota
        exhaustion). None if not configured."""
        key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_TEXT_MODEL")
        if not key or not model:
            return None
        thinking = os.environ.get("GEMINI_THINKING_LEVEL", "LOW").strip().upper() or None
        fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "").strip() or None
        return cls(api_key=key, model=model, thinking_level=thinking, fallback_model=fallback)

    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> ModelReply:
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
            max_output_tokens=1024,
            thinking_config=self._thinking,
            # Extraction never calls tools; also silences the SDK's AFC warning.
            automatic_function_calling=self._types.AutomaticFunctionCallingConfig(disable=True),
        )
        use_main = self._fallback is None or self._monotonic() >= self._main_blocked_until
        model = self._model if use_main else self._fallback
        try:
            response = await self._client.aio.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as err:  # SDK raises several error families; all mean "no usable output"
            if not (use_main and self._fallback and getattr(err, "code", None) == 429):
                raise ModelError(type(err).__name__) from err
            self._main_blocked_until = self._monotonic() + self.QUOTA_COOLDOWN_SECONDS
            model = self._fallback
            try:
                response = await self._client.aio.models.generate_content(model=model, contents=prompt, config=config)
            except Exception as fallback_err:
                raise ModelError(type(fallback_err).__name__) from fallback_err
        text = response.text
        if not text:
            raise ModelError("EMPTY_RESPONSE")
        usage = response.usage_metadata
        return ModelReply(
            text=text,
            provider="gemini",
            model=response.model_version or model,  # the model that actually answered, for honest telemetry
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
