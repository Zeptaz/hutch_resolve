"""Gemini quota exhaustion (HTTP 429) switches to the fallback model instead of becoming an outage."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")

from resolve.conversation.model import GeminiModelClient, ModelError  # noqa: E402


class QuotaError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


def client(responses: dict[str, list], now: list[float]) -> tuple[GeminiModelClient, list[str]]:
    c = GeminiModelClient(api_key="test-not-a-key", model="main-model", fallback_model="backup-model", monotonic=lambda: now[0])
    calls: list[str] = []

    async def generate_content(*, model, contents, config):
        calls.append(model)
        result = responses[model].pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(text='{"ok": true}', model_version=None, usage_metadata=None)

    c._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    return c, calls


def ask(c: GeminiModelClient):
    return asyncio.run(c.generate_json(system="s", prompt="p", schema={"type": "object"}))


def test_quota_error_answers_with_the_fallback_and_reports_it() -> None:
    now = [0.0]
    c, calls = client({"main-model": [QuotaError(429)], "backup-model": ["ok", "ok"]}, now)
    assert ask(c).model == "backup-model"
    now[0] = 60.0
    assert ask(c).model == "backup-model"  # still cooling down: no wasted call to the empty quota
    assert calls == ["main-model", "backup-model", "backup-model"]


def test_main_model_is_tried_again_after_the_cooldown() -> None:
    now = [0.0]
    c, calls = client({"main-model": [QuotaError(429), "ok"], "backup-model": ["ok"]}, now)
    ask(c)
    now[0] = GeminiModelClient.QUOTA_COOLDOWN_SECONDS + 1
    assert ask(c).model == "main-model" and calls == ["main-model", "backup-model", "main-model"]


def test_other_errors_do_not_switch_models() -> None:
    c, calls = client({"main-model": [QuotaError(500)], "backup-model": ["ok"]}, [0.0])
    with pytest.raises(ModelError):
        ask(c)
    assert calls == ["main-model"]
