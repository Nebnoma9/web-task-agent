"""Anthropic Messages API."""

from __future__ import annotations

from typing import Any

from wta.providers.base import ChatMessage, ChatProvider, Completion, ProviderError, b64


class AnthropicProvider(ChatProvider):
    name = "anthropic"

    def __init__(
        self, *, api_key: str, base_url: str = "https://api.anthropic.com", **kw: Any
    ) -> None:
        super().__init__(**kw)
        self._key = api_key
        self._base = base_url.rstrip("/")

    def _url(self) -> str:
        return f"{self._base}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": "2023-06-01"}

    def _payload(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        system = "\n\n".join(m.text for m in messages if m.role == "system")
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            content: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64(i)},
                }
                for i in m.images
            ]
            content.append({"type": "text", "text": m.text})
            out.append({"role": m.role, "content": content})
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": out,
        }
        if system:
            payload["system"] = system
        return payload

    def _parse(self, data: dict[str, Any]) -> Completion:
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderError(f"anthropic: unexpected response shape: {data!r:.200}")
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=data.get("model", self.model),
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
        )
