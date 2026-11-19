"""Harness-side success checks. They look at the real page state, not at what the agent says."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import Page

from wta.actions import Done
from wta.tasks import (
    AgentVerdict,
    Criterion,
    LocalStorage,
    SelectorText,
    SummaryMentions,
    TextAbsent,
    TextVisible,
    UrlContains,
)

_TEXT_VISIBLE_JS = "t => !!document.body && document.body.innerText.includes(t)"


@dataclass(slots=True)
class CheckResult:
    type: str
    passed: bool
    detail: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


async def evaluate_criterion(c: Criterion, page: Page, final: Done | None) -> CheckResult:
    match c:
        case UrlContains(value=value):
            ok = value in page.url
            return CheckResult(c.type, ok, f"url={page.url}", c.description)
        case TextVisible(text=text):
            ok = bool(await page.evaluate(_TEXT_VISIBLE_JS, text))
            return CheckResult(
                c.type, ok, f"{text!r} {'visible' if ok else 'not visible'}", c.description
            )
        case TextAbsent(text=text):
            seen = bool(await page.evaluate(_TEXT_VISIBLE_JS, text))
            return CheckResult(
                c.type, not seen, f"{text!r} {'visible' if seen else 'absent'}", c.description
            )
        case SelectorText(selector=selector, contains=contains):
            texts = await page.locator(selector).all_inner_texts()
            ok = any(contains in t for t in texts)
            return CheckResult(
                c.type, ok, f"{selector} texts={[t[:60] for t in texts][:5]}", c.description
            )
        case LocalStorage(key=key, path=path, equals=equals, contains=contains, exists=exists):
            raw = await page.evaluate("k => localStorage.getItem(k)", key)
            if raw is None:
                return CheckResult(
                    c.type, not exists, f"localStorage[{key!r}] missing", c.description
                )
            try:
                stored: Any = json.loads(raw)
            except json.JSONDecodeError:
                stored = raw
            try:
                stored = _dig(stored, path) if path else stored
            except (KeyError, IndexError, ValueError):
                return CheckResult(c.type, not exists, f"path {path!r} missing", c.description)
            if not exists:
                return CheckResult(c.type, False, f"value present: {stored!r:.80}", c.description)
            ok = True
            if equals is not None:
                ok = ok and stored == equals
            if contains is not None:
                ok = ok and contains in json.dumps(stored)
            return CheckResult(c.type, ok, f"{key}.{path}={stored!r:.80}", c.description)
        case AgentVerdict(expected=expected):
            if final is None:
                return CheckResult(c.type, False, "agent never called done", c.description)
            ok = final.success == expected
            return CheckResult(
                c.type,
                ok,
                f"agent said success={final.success}, expected {expected}",
                c.description,
            )
        case SummaryMentions(keywords=keywords):
            summary = (final.summary if final else "").lower()
            missing = [k for k in keywords if k.lower() not in summary]
            return CheckResult(
                c.type,
                not missing,
                f"missing={missing}" if missing else "all keywords present",
                c.description,
            )
    raise AssertionError(f"unhandled criterion {c!r}")  # pragma: no cover


async def evaluate_all(
    criteria: list[Criterion], page: Page, final: Done | None
) -> list[CheckResult]:
    out = []
    for c in criteria:
        try:
            out.append(await evaluate_criterion(c, page, final))
        except Exception as exc:  # a broken check is a failed check, never a crash
            out.append(CheckResult(c.type, False, f"check error: {exc}", c.description))
    return out
