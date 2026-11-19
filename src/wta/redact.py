"""Redaction for anything that leaves the process: traces, reports, console output.

Three layers:
1. Known secret values (task secrets, API keys from the environment) are replaced by name.
2. Card-number-like digit runs are masked to their last four digits.
3. Values typed into password or payment fields are replaced wholesale by the executor
   (see ``wta.browser.BrowserSession._fill``) - the trace never contains them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*secret:([a-zA-Z0-9_]+)\s*\}\}")


def substitute_secrets(text: str, secrets: Mapping[str, str]) -> str:
    """Replace ``{{secret:name}}`` placeholders with real values (execution time only)."""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in secrets:
            raise KeyError(f"unknown secret placeholder {name!r}")
        return secrets[name]

    return _PLACEHOLDER_RE.sub(repl, text)


def has_secret_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text))


class Redactor:
    def __init__(
        self,
        secrets: Mapping[str, str] | None = None,
        *,
        include_env: bool = True,
        mask_cards: bool = True,
    ) -> None:
        self.mask_cards = mask_cards
        self._values: dict[str, str] = {}
        for name, value in (secrets or {}).items():
            self.add(name, value)
        if include_env:
            for k, v in os.environ.items():
                if any(h in k.upper() for h in _SECRET_ENV_HINTS) and len(v) >= 8:
                    self.add(k, v)

    def add(self, name: str, value: str) -> None:
        if value and len(value) >= 4:
            self._values[value] = name

    def extend(self, items: Iterable[tuple[str, str]]) -> None:
        for name, value in items:
            self.add(name, value)

    def text(self, s: str) -> str:
        # Longest first so that overlapping secrets are fully covered.
        for value in sorted(self._values, key=len, reverse=True):
            if value in s:
                s = s.replace(value, f"[REDACTED:{self._values[value]}]")
        return _CARD_RE.sub(_mask_card, s) if self.mask_cards else s

    def obj(self, o: Any) -> Any:
        """Recursively redact strings inside JSON-like structures."""
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [self.obj(v) for v in o]
        return o


def _mask_card(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return f"[CARD ****{digits[-4:]}]"
