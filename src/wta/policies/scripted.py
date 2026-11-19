"""Deterministic reference policy.

It replays the ``scripted_plan`` of a task, resolving each ``target`` (role + accessible name) to a
ref in the *live* snapshot at every step. It needs no API key, so CI can run the full harness
(browser, snapshot, guardrails, executor, success checks, traces, report) end to end.

It measures the harness, not model quality: a 100% scripted success rate means the plumbing and
the success criteria are consistent, nothing more.
"""

from __future__ import annotations

import time
from typing import Any

from wta.actions import Action, Done, parse_action
from wta.policies.base import Decision, PolicyContext
from wta.snapshot import Snapshot
from wta.tasks import ScriptStep, Target, Task


class ScriptedPolicy:
    name = "scripted"

    def __init__(self) -> None:
        self._plan: list[ScriptStep] = []
        self._i = 0
        self._console: list[str] = []

    @property
    def wants_screenshot(self) -> bool:
        return False

    def reset(self, task: Task) -> None:
        if not task.scripted_plan:
            raise ValueError(f"task {task.id} has no scripted_plan")
        self._plan = list(task.scripted_plan)
        self._i = 0
        self._console = []

    async def decide(self, ctx: PolicyContext) -> Decision:
        t0 = time.perf_counter()
        action, thought = self._next(ctx)
        return Decision(action=action, thought=thought, latency_ms=(time.perf_counter() - t0) * 1e3)

    def _next(self, ctx: PolicyContext) -> tuple[Action, str]:
        self._console.extend(ctx.observation.console_errors)
        if self._i >= len(self._plan):
            return Done(success=False, summary="scripted plan exhausted without done"), "plan end"
        step = self._plan[self._i]
        self._i += 1

        if step.type == "done_from_checks":
            checks = [r for r in ctx.history if r.result.assertion is not None]
            failed = [r for r in checks if r.result.assertion is False]
            details = "; ".join(r.result.message for r in failed) or "all checks passed"
            summary = f"{step.summary or 'QA check'} -> {details}"
            if failed and self._console:
                summary += f" | console: {self._console[-1][:200]}"
            return (
                Done(success=not failed, summary=summary),
                f"{len(checks)} checks, {len(failed)} failed",
            )

        payload: dict[str, Any] = {
            k: v for k, v in step.model_dump(exclude={"target"}).items() if v is not None
        }
        thought = f"plan step {self._i}/{len(self._plan)}: {step.type}"
        if step.target is not None:
            ref = _resolve(ctx.observation.snapshot, step.target)
            if ref is None:
                return (
                    Done(
                        success=False,
                        summary=f"scripted target not found: {_describe(step.target)}",
                    ),
                    "target missing",
                )
            payload["ref"] = ref
            thought += f" -> {_describe(step.target)} = {ref}"
        action, _ = parse_action(payload)  # same validation path as model output
        return action, thought


def _resolve(snap: Snapshot, target: Target) -> str | None:
    matches = snap.find(target.role, name=target.name, name_contains=target.name_contains)
    if len(matches) <= target.nth:
        return None
    return matches[target.nth].ref


def _describe(t: Target) -> str:
    name = t.name if t.name is not None else f"*{t.name_contains}*"
    return f"{t.role} {name!r}" + (f" #{t.nth}" if t.nth else "")
