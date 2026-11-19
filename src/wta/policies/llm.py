"""LLM policy: prompt a chat model with the snapshot (and optionally a screenshot) and parse a
single JSON action. Invalid output is rejected with the validation error and re-asked."""

from __future__ import annotations

import time

from wta.actions import ActionParseError, parse_action
from wta.policies.base import Decision, PolicyContext, PolicyError, StepRecord
from wta.providers.base import ChatMessage, ChatProvider
from wta.redact import Redactor
from wta.tasks import Task

SYSTEM_PROMPT = """You are a careful web automation agent operating a real browser.
Each turn you receive the task, a compact accessibility snapshot of the current page, the
result of your previous action, and (sometimes) a screenshot. Elements are listed as
`[eN] role "accessible name" ...`; refer to them ONLY by their ref (e.g. "e12").
Refs change after every observation - never reuse a ref from an older snapshot.

Respond with exactly ONE JSON object and nothing else:
{"thought": "<one short sentence>", "action": <action>}

Allowed actions:
  {"type": "goto", "url": "<absolute URL or path like /shop/index.html>"}
  {"type": "click", "ref": "eN"}
  {"type": "fill", "ref": "eN", "text": "<value>"}         (replaces the field's value)
  {"type": "select", "ref": "eN", "option": "<visible option label>"}
  {"type": "press", "key": "Enter|Tab|Escape|ArrowDown|...", "ref": "eN" (optional)}
  {"type": "scroll", "direction": "down|up", "amount": 600}
  {"type": "wait_for", "text": "<text>", "timeout_ms": 3000}
  {"type": "assert_text", "text": "<text>", "present": true}
  {"type": "done", "success": true|false, "summary": "<what you did / what you found>"}

Rules:
- Only use credentials written as {{secret:name}} placeholders, verbatim; they are substituted
  safely at execution time. Never invent credentials or payment data.
- After an action, check its result. "no_visible_change" after a click means nothing happened.
- For QA/verification tasks, `done.success` is your verdict: true if the site behaves as the
  task says it should, false if you found a defect; describe the defect in `summary`.
- Use `done` as soon as the goal is achieved or clearly impossible. Do not loop.
"""


def _history_text(history: list[StepRecord], redactor: Redactor, limit: int = 8) -> str:
    if not history:
        return "(no previous actions)"
    lines = []
    for r in history[-limit:]:
        act = redactor.obj(r.result.logged_action or r.action.model_dump())
        lines.append(f"step {r.step}: {act} -> {redactor.text(r.result.describe())}")
    return "\n".join(lines)


class LLMPolicy:
    def __init__(
        self,
        provider: ChatProvider,
        *,
        use_vision: bool = True,
        max_invalid: int = 3,
        redactor: Redactor | None = None,
    ) -> None:
        self.provider = provider
        self.name = f"llm:{provider.name}:{provider.model}"
        self.use_vision = use_vision and provider.supports_vision
        self.max_invalid = max_invalid
        # Prompts keep public test card numbers visible (the task may need them); real secrets
        # never reach the prompt because they only exist as {{secret:...}} placeholders.
        self.redactor = redactor or Redactor(mask_cards=False)

    @property
    def wants_screenshot(self) -> bool:
        return self.use_vision

    def reset(self, task: Task) -> None:
        return None

    def build_messages(self, ctx: PolicyContext) -> list[ChatMessage]:
        obs = ctx.observation
        console = (
            "\n".join(obs.console_errors[-5:]) if obs.console_errors else "(none since last step)"
        )
        user = (
            f"TASK ({ctx.task.kind}): {ctx.task.goal}\n"
            f"Start URL: {ctx.task.start_url}\n"
            f"Step {ctx.step} of at most {ctx.max_steps}.\n\n"
            f"PREVIOUS ACTIONS:\n{_history_text(ctx.history, self.redactor)}\n\n"
            f"CONSOLE ERRORS:\n{console}\n\n"
            f"CURRENT PAGE SNAPSHOT:\n{obs.snapshot.render()}\n\n"
            "Reply with one JSON object."
        )
        images = [obs.screenshot_png] if (self.use_vision and obs.screenshot_png) else []
        return [
            ChatMessage("system", SYSTEM_PROMPT),
            ChatMessage("user", self.redactor.text(user), images=images),
        ]

    async def decide(self, ctx: PolicyContext) -> Decision:
        messages = self.build_messages(ctx)
        t0 = time.perf_counter()
        prompt_tokens = completion_tokens = 0
        errors: list[str] = []
        for _ in range(self.max_invalid + 1):
            completion = await self.provider.complete(messages)
            prompt_tokens += completion.prompt_tokens
            completion_tokens += completion.completion_tokens
            try:
                action, thought = parse_action(completion.text)
            except ActionParseError as exc:
                errors.append(str(exc))
                messages = [
                    *messages,
                    ChatMessage("assistant", completion.text[:2000]),
                    ChatMessage(
                        "user",
                        f"Your reply was rejected: {exc}. Reply again with exactly one valid "
                        'JSON object {"thought": ..., "action": {...}} using the allowed actions.',
                    ),
                ]
                continue
            return Decision(
                action=action,
                thought=thought,
                latency_ms=(time.perf_counter() - t0) * 1e3,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                invalid_attempts=len(errors),
                invalid_errors=errors,
            )
        raise PolicyError(
            f"no valid action after {self.max_invalid + 1} attempts; last error: {errors[-1]}"
        )
