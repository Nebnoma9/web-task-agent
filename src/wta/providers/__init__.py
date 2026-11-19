"""Provider factory. Configuration comes from environment variables (see .env.example)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from wta.providers.anthropic import AnthropicProvider
from wta.providers.base import ChatMessage, ChatProvider, Completion, ProviderError
from wta.providers.cohere import CohereProvider
from wta.providers.ollama import OllamaProvider
from wta.providers.openai import OpenAIProvider

PROVIDERS = ("openai", "anthropic", "cohere", "ollama")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "cohere": "command-a-vision-07-2025",
    "ollama": "llama3.2-vision",
}

__all__ = [
    "DEFAULT_MODELS",
    "PROVIDERS",
    "ChatMessage",
    "ChatProvider",
    "Completion",
    "ProviderError",
    "make_provider",
]


def _require(env: dict[str, str], key: str, provider: str) -> str:
    val = env.get(key)
    if not val:
        raise ProviderError(f"{provider}: environment variable {key} is not set")
    return val


def _vision_flag(env: dict[str, str], default: bool) -> bool:
    raw = env.get("WTA_VISION")
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def make_provider(
    name: str,
    *,
    model: str | None = None,
    env: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatProvider:
    """Build a provider from env. ``WTA_VISION=0`` disables screenshots for any provider."""
    env = dict(os.environ) if env is None else env
    name = name.lower()
    model = model or env.get(f"WTA_{name.upper()}_MODEL") or DEFAULT_MODELS.get(name, "")
    common: dict[str, Any] = {"model": model, "transport": transport}
    if name == "openai":
        return OpenAIProvider(
            api_key=_require(env, "OPENAI_API_KEY", name),
            base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            supports_vision=_vision_flag(env, True),
            **common,
        )
    if name == "anthropic":
        return AnthropicProvider(
            api_key=_require(env, "ANTHROPIC_API_KEY", name),
            supports_vision=_vision_flag(env, True),
            **common,
        )
    if name == "cohere":
        return CohereProvider(
            api_key=_require(env, "COHERE_API_KEY", name),
            supports_vision=_vision_flag(env, "vision" in model),
            **common,
        )
    if name == "ollama":
        vision_models = ("llava", "vision", "-vl", "vl:", "bakllava", "moondream", "gemma3")
        return OllamaProvider(
            host=env.get("OLLAMA_HOST", "http://localhost:11434"),
            supports_vision=_vision_flag(env, any(v in model for v in vision_models)),
            **common,
        )
    raise ProviderError(f"unknown provider {name!r}; choose one of {PROVIDERS}")
