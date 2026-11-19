"""One interface for every model provider.

Providers are thin ``httpx`` clients over each vendor's public HTTP API - no vendor SDKs - so the
request/response mapping is explicit, easy to audit and easy to unit-test with a mock transport.
"""

from __future__ import annotations

import asyncio
import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    text: str
    images: list[bytes] = field(default_factory=list)  # PNG bytes


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


def b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


class ChatProvider(ABC):
    """Base class: subclasses implement ``_payload``, ``_headers``, ``_url`` and ``_parse``."""

    name: str = "base"

    def __init__(
        self,
        *,
        model: str,
        supports_vision: bool,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.supports_vision = supports_vision
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 600
    ) -> Completion:
        if not self.supports_vision:
            messages = [ChatMessage(m.role, m.text) for m in messages]
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
        t0 = time.perf_counter()
        resp: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.post(self._url(), headers=self._headers(), json=payload)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise ProviderError(f"{self.name}: transport error: {exc}") from exc
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 529) and attempt < self.max_retries:
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            break
        assert resp is not None
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
        completion = self._parse(resp.json())
        completion.latency_ms = (time.perf_counter() - t0) * 1e3
        return completion

    @abstractmethod
    def _url(self) -> str: ...

    @abstractmethod
    def _headers(self) -> dict[str, str]: ...

    @abstractmethod
    def _payload(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, Any]: ...

    @abstractmethod
    def _parse(self, data: dict[str, Any]) -> Completion: ...
