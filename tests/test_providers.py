"""Provider request/response mapping, tested against httpx mock transports (no network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from wta.providers import ChatMessage, ProviderError, make_provider

MSGS = [
    ChatMessage("system", "be careful"),
    ChatMessage("user", "snapshot here", images=[b"\x89PNG"]),
]


def _transport(reply: dict[str, Any], seen: list[httpx.Request], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply)

    return httpx.MockTransport(handler)


async def test_openai_mapping() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "model": "gpt-4o-mini",
        "choices": [{"message": {"content": '{"type": "click", "ref": "e1"}'}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }
    p = make_provider("openai", env={"OPENAI_API_KEY": "sk-x"}, transport=_transport(reply, seen))
    c = await p.complete(MSGS)
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-x"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert (c.text, c.prompt_tokens, c.completion_tokens) == (
        '{"type": "click", "ref": "e1"}',
        11,
        7,
    )


async def test_anthropic_mapping() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "model": "claude-x",
        "content": [{"type": "text", "text": "{}"}],
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }
    p = make_provider(
        "anthropic", env={"ANTHROPIC_API_KEY": "k"}, transport=_transport(reply, seen)
    )
    c = await p.complete(MSGS)
    body = json.loads(seen[0].content)
    assert seen[0].headers["x-api-key"] == "k"
    assert body["system"] == "be careful"
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["messages"][0]["content"][0]["type"] == "image"
    assert (c.prompt_tokens, c.completion_tokens) == (5, 2)


async def test_cohere_mapping_text_only_model_drops_images() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "message": {"content": [{"type": "text", "text": "{}"}]},
        "usage": {"tokens": {"input_tokens": 9, "output_tokens": 3}},
    }
    p = make_provider(
        "cohere",
        model="command-r-plus",
        env={"COHERE_API_KEY": "k"},
        transport=_transport(reply, seen),
    )
    assert not p.supports_vision
    c = await p.complete(MSGS)
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.cohere.com/v2/chat"
    assert body["messages"][1]["content"] == "snapshot here"  # no image for a text model
    assert (c.prompt_tokens, c.completion_tokens) == (9, 3)


async def test_ollama_mapping() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "model": "llava",
        "message": {"content": "{}"},
        "prompt_eval_count": 4,
        "eval_count": 1,
    }
    p = make_provider(
        "ollama",
        model="llava:13b",
        env={"OLLAMA_HOST": "http://gpu-box:11434"},
        transport=_transport(reply, seen),
    )
    assert p.supports_vision
    c = await p.complete(MSGS)
    body = json.loads(seen[0].content)
    assert seen[0].url == "http://gpu-box:11434/api/chat"
    assert body["format"] == "json" and body["stream"] is False
    assert body["messages"][1]["images"]
    assert c.prompt_tokens == 4


async def test_retry_then_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="overloaded")

    p = make_provider("openai", env={"OPENAI_API_KEY": "k"}, transport=httpx.MockTransport(handler))
    p.max_retries = 1
    with pytest.raises(ProviderError, match="HTTP 503"):
        await p.complete(MSGS)
    assert calls == 2


def test_missing_key_and_unknown_provider() -> None:
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        make_provider("openai", env={})
    with pytest.raises(ProviderError, match="unknown provider"):
        make_provider("palm", env={})


def test_vision_can_be_disabled_by_env() -> None:
    p = make_provider("anthropic", env={"ANTHROPIC_API_KEY": "k", "WTA_VISION": "0"})
    assert not p.supports_vision
