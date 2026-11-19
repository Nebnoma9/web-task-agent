"""Cohere Chat API v2. Images are sent only for vision-capable models (e.g. command-a-vision)."""

from __future__ import annotations

from typing import Any

from wta.providers.base import ChatMessage, ChatProvider, Completion, ProviderError, b64


class CohereProvider(ChatProvider):
    name = "cohere"

    def __init__(self, *, api_key: str, base_url: str = "https://api.cohere.com", **kw: Any):
        super().__init__(**kw)
        self._key = api_key
        self._base = base_url.rstrip("/")

    def _url(self) -> str:
        return f"{self._base}/v2/chat"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def _payload(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.images:
                content: Any = [{"type": "text", "text": m.text}] + [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(i)}"}}
                    for i in m.images
                ]
            else:
                content = m.text
            out.append({"role": m.role, "content": content})
        return {
            "model": self.model,
            "messages": out,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

    def _parse(self, data: dict[str, Any]) -> Completion:
        try:
            parts = data["message"]["content"]
        except KeyError as exc:
            raise ProviderError(f"cohere: unexpected response shape: {data!r:.200}") from exc
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        tokens = (data.get("usage") or {}).get("tokens") or {}
        return Completion(
            text=text,
            model=self.model,
            prompt_tokens=int(tokens.get("input_tokens", 0)),
            completion_tokens=int(tokens.get("output_tokens", 0)),
        )
