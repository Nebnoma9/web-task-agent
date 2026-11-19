"""OpenAI Chat Completions API (also works with OpenAI-compatible servers via OPENAI_BASE_URL)."""

from __future__ import annotations

from typing import Any

from wta.providers.base import ChatMessage, ChatProvider, Completion, ProviderError, b64


class OpenAIProvider(ChatProvider):
    name = "openai"

    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1", **kw: Any):
        super().__init__(**kw)
        self._key = api_key
        self._base = base_url.rstrip("/")

    def _url(self) -> str:
        return f"{self._base}/chat/completions"

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
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"openai: unexpected response shape: {data!r:.200}") from exc
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=data.get("model", self.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )
