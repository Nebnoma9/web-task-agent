"""Ollama local models via /api/chat (JSON mode). Images go to vision models such as llava."""

from __future__ import annotations

from typing import Any

from wta.providers.base import ChatMessage, ChatProvider, Completion, ProviderError, b64


class OllamaProvider(ChatProvider):
    name = "ollama"

    def __init__(self, *, host: str = "http://localhost:11434", **kw: Any) -> None:
        kw.setdefault("timeout_s", 180.0)
        super().__init__(**kw)
        self._host = host.rstrip("/")

    def _url(self) -> str:
        return f"{self._host}/api/chat"

    def _headers(self) -> dict[str, str]:
        return {}

    def _payload(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.text}
            if m.images:
                msg["images"] = [b64(i) for i in m.images]
            out.append(msg)
        return {
            "model": self.model,
            "messages": out,
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

    def _parse(self, data: dict[str, Any]) -> Completion:
        try:
            text = data["message"]["content"]
        except KeyError as exc:
            raise ProviderError(f"ollama: unexpected response shape: {data!r:.200}") from exc
        return Completion(
            text=text,
            model=data.get("model", self.model),
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
        )
