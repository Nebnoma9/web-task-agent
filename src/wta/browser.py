"""Browser session: launches Chromium, observes pages, and executes + verifies actions."""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Page,
    Playwright,
    Route,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from wta.actions import (
    Action,
    AssertText,
    Click,
    Done,
    Fill,
    Goto,
    Press,
    Scroll,
    Select,
    WaitFor,
)
from wta.guardrails import FieldInfo, Guardrails, GuardrailViolation
from wta.redact import has_secret_placeholder, substitute_secrets
from wta.snapshot import REF_ATTR, Snapshot, build_snapshot

CHROMIUM_ENV = "WTA_CHROMIUM_EXECUTABLE"
_MASK_SELECTOR = "input[type=password], [autocomplete^='cc-']"

_TEXT_VISIBLE_JS = "t => !!document.body && document.body.innerText.includes(t)"

_FIELD_INFO_JS = """el => ({
  tag: el.tagName.toLowerCase(),
  input_type: (el.getAttribute('type') || '').toLowerCase(),
  name: el.getAttribute('name') || '',
  id: el.id || '',
  autocomplete: el.getAttribute('autocomplete') || '',
  label: ((el.labels && el.labels[0] && el.labels[0].textContent) || el.getAttribute('aria-label')
          || el.getAttribute('placeholder') || '').trim(),
})"""


async def launch_browser(pw: Playwright, *, headless: bool = True) -> Browser:
    """Launch Chromium. ``WTA_CHROMIUM_EXECUTABLE`` overrides the bundled browser path, which is
    handy when the installed Playwright version and the pre-installed browser build differ."""
    exe = os.environ.get(CHROMIUM_ENV) or None
    return await pw.chromium.launch(headless=headless, executable_path=exe)


@dataclass(slots=True)
class Observation:
    snapshot: Snapshot
    console_errors: list[str] = field(default_factory=list)
    screenshot_png: bytes | None = None
    screenshot_path: str | None = None


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str = ""
    error: str | None = None
    blocked_by: str | None = None
    effect: str | None = None  # "changed" | "no_visible_change" for click/press
    assertion: bool | None = None  # outcome of assert_text / wait_for
    logged_action: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if self.blocked_by:
            return f"BLOCKED by guardrail '{self.blocked_by}': {self.error}"
        if not self.ok:
            return f"FAILED: {self.error}"
        extra = f" (effect: {self.effect})" if self.effect else ""
        return f"OK: {self.message}{extra}"


class BrowserSession:
    """One isolated browser context per task run (fresh cookies/localStorage)."""

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        *,
        guard: Guardrails,
        base_url: str,
        secrets: dict[str, str] | None = None,
        action_timeout_ms: int = 5000,
    ) -> None:
        self.context = context
        self.page = page
        self.guard = guard
        self.base_url = base_url
        self.secrets = secrets or {}
        self.action_timeout_ms = action_timeout_ms
        self._console_errors: list[str] = []
        self._last_snapshot: Snapshot | None = None
        page.on("console", self._on_console)
        page.on("pageerror", lambda exc: self._console_errors.append(f"pageerror: {exc}"))

    # -- lifecycle ---------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        browser: Browser,
        *,
        guard: Guardrails,
        base_url: str,
        secrets: dict[str, str] | None = None,
    ) -> BrowserSession:
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        guard_ref = guard

        async def _route(route: Route) -> None:
            if guard_ref.request_allowed(route.request.url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await context.route("**/*", _route)
        page = await context.new_page()
        return cls(context, page, guard=guard, base_url=base_url, secrets=secrets)

    async def close(self) -> None:
        await self.context.close()

    def _on_console(self, msg: ConsoleMessage) -> None:
        if msg.type == "error":
            self._console_errors.append(f"console.error: {msg.text}")

    # -- observe -----------------------------------------------------------------------------

    async def snapshot(self) -> Snapshot:
        last_exc: Exception | None = None
        for _ in range(4):
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                snap = await build_snapshot(self.page)
                self._last_snapshot = snap
                return snap
            except PlaywrightError as exc:  # navigation raced the evaluate; retry
                last_exc = exc
                await asyncio.sleep(0.15)
        raise RuntimeError(f"could not snapshot page: {last_exc}")

    async def observe(self, *, screenshot_path: Path | None = None) -> Observation:
        snap = await self.snapshot()
        errors, self._console_errors = self._console_errors, []
        png: bytes | None = None
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            png = await self.page.screenshot(
                path=str(screenshot_path), mask=[self.page.locator(_MASK_SELECTOR)]
            )
        return Observation(
            snapshot=snap,
            console_errors=errors,
            screenshot_png=png,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )

    async def text_visible(self, text: str) -> bool:
        return bool(await self.page.evaluate(_TEXT_VISIBLE_JS, text))

    # -- act + verify ------------------------------------------------------------------------

    async def execute(self, action: Action) -> ActionResult:
        logged = action.model_dump()
        try:
            result = await self._dispatch(action)
        except GuardrailViolation as v:
            result = ActionResult(ok=False, error=v.message, blocked_by=v.rule)
        except (PlaywrightError, KeyError) as exc:
            msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            result = ActionResult(ok=False, error=msg[:300])
        if isinstance(action, Fill) and not result.logged_action:
            # Fill never got far enough to classify the field; keep placeholders, mask the rest.
            logged["text"] = (
                action.text if has_secret_placeholder(action.text) else "[REDACTED:unverified]"
            )
        result.logged_action = result.logged_action or logged
        return result

    def _locator(self, ref: str) -> Any:
        return self.page.locator(f'[{REF_ATTR}="{ref}"]')

    async def _require(self, ref: str) -> Any:
        loc = self._locator(ref)
        if await loc.count() == 0:
            raise KeyError(f"ref {ref} not found on the current page (stale ref? observe again)")
        return loc.first

    async def _settle(self) -> None:
        with contextlib.suppress(PlaywrightError):
            await self.page.wait_for_load_state("domcontentloaded", timeout=3000)
        await asyncio.sleep(0.25)

    async def _effect(self, before: str | None) -> str:
        await self._settle()
        after = (await self.snapshot()).fingerprint
        return "no_visible_change" if before == after else "changed"

    async def _dispatch(self, action: Action) -> ActionResult:
        before = self._last_snapshot.fingerprint if self._last_snapshot else None
        t = self.action_timeout_ms
        match action:
            case Goto(url=url):
                target = self.guard.resolve_url(url, self.base_url)
                resp = await self.page.goto(target, wait_until="domcontentloaded", timeout=t * 2)
                status = resp.status if resp else 0
                if status >= 400:
                    return ActionResult(ok=False, error=f"HTTP {status} for {target}")
                return ActionResult(ok=True, message=f"navigated to {target}")
            case Click(ref=ref):
                loc = await self._require(ref)
                await loc.click(timeout=t)
                return ActionResult(
                    ok=True, message=f"clicked {ref}", effect=await self._effect(before)
                )
            case Fill(ref=ref, text=text):
                return await self._fill(ref, text)
            case Select(ref=ref, option=option):
                loc = await self._require(ref)
                try:
                    await loc.select_option(label=option, timeout=t)
                except PlaywrightError:
                    await loc.select_option(value=option, timeout=t)
                await self._settle()
                return ActionResult(ok=True, message=f"selected {option!r} in {ref}")
            case Press(key=key, ref=ref):
                pw_key = " " if key == "Space" else key
                if ref:
                    await (await self._require(ref)).press(pw_key, timeout=t)
                else:
                    await self.page.keyboard.press(pw_key)
                return ActionResult(
                    ok=True, message=f"pressed {key}", effect=await self._effect(before)
                )
            case Scroll(direction=direction, amount=amount, ref=ref):
                if ref:
                    await (await self._require(ref)).scroll_into_view_if_needed(timeout=t)
                else:
                    await self.page.mouse.wheel(0, amount if direction == "down" else -amount)
                await asyncio.sleep(0.1)
                return ActionResult(ok=True, message="scrolled")
            case WaitFor(text=text, timeout_ms=timeout_ms):
                try:
                    await self.page.wait_for_function(
                        _TEXT_VISIBLE_JS, arg=text, timeout=timeout_ms
                    )
                    return ActionResult(ok=True, message=f"text {text!r} appeared", assertion=True)
                except PlaywrightError:
                    return ActionResult(
                        ok=True,
                        message=f"text {text!r} did NOT appear within {timeout_ms} ms",
                        assertion=False,
                    )
            case AssertText(text=text, present=present):
                visible = await self.text_visible(text)
                passed = visible == present
                verb = "visible" if visible else "not visible"
                return ActionResult(
                    ok=True,
                    message=f"assertion {'passed' if passed else 'FAILED'}: {text!r} is {verb}",
                    assertion=passed,
                )
            case Done():
                return ActionResult(ok=True, message="done")
        raise AssertionError(f"unhandled action {action!r}")  # pragma: no cover

    async def _fill(self, ref: str, text: str) -> ActionResult:
        loc = await self._require(ref)
        info = FieldInfo(**await loc.evaluate(_FIELD_INFO_JS))
        value = substitute_secrets(text, self.secrets)
        sensitive = info.is_password() or info.is_payment()
        if has_secret_placeholder(text):
            logged_text = text  # the placeholder itself is safe to log
        elif sensitive:
            logged_text = f"[REDACTED:{'password' if info.is_password() else 'payment'}-field]"
        else:
            logged_text = text
        logged = {"type": "fill", "ref": ref, "text": logged_text}
        try:
            self.guard.check_fill(info, value)
        except GuardrailViolation as v:
            return ActionResult(ok=False, error=v.message, blocked_by=v.rule, logged_action=logged)
        await loc.fill(value, timeout=self.action_timeout_ms)
        actual = await loc.input_value()
        if actual != value:
            return ActionResult(
                ok=False,
                error="value did not stick after fill (field may be read-only or reformatted)",
                logged_action=logged,
            )
        return ActionResult(ok=True, message=f"filled {ref}", logged_action=logged)
