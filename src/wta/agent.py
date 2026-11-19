"""The agent loop: observe -> plan -> act -> verify, with guardrails and tracing at every step."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from wta.actions import Done, Goto
from wta.browser import ActionResult, BrowserSession
from wta.checks import CheckResult, evaluate_all
from wta.guardrails import GuardrailViolation
from wta.policies.base import Policy, PolicyContext, PolicyError, StepRecord
from wta.providers.base import ProviderError
from wta.tasks import Task
from wta.trace import Tracer

Status = Literal["completed", "max_steps", "policy_error", "error"]


@dataclass(slots=True)
class EpisodeResult:
    task_id: str
    task_title: str
    kind: str
    repeat: int
    policy: str
    status: Status
    passed: bool
    steps: int
    duration_s: float
    agent_success: bool | None
    agent_summary: str
    checks: list[CheckResult]
    failure_reason: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    invalid_actions: int = 0
    guardrail_blocks: int = 0
    trace_path: str = ""
    step_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _failure_reason(status: Status, checks: list[CheckResult], error: str | None) -> str | None:
    if status == "max_steps":
        return "step budget exhausted before the agent called done"
    if status in ("policy_error", "error"):
        return error or status
    failed = [c for c in checks if not c.passed]
    if failed:
        return "; ".join(f"{c.type}: {c.detail}" for c in failed)
    return None


async def run_episode(
    task: Task,
    policy: Policy,
    session: BrowserSession,
    tracer: Tracer,
    *,
    repeat: int = 1,
    screenshots_dir: Path | None = None,
    screenshot_rel_to: Path | None = None,
) -> EpisodeResult:
    """Run one task once. The episode passes only if the agent terminates *and* every
    harness-side success criterion holds on the final page state."""
    guard = session.guard
    policy.reset(task)
    history: list[StepRecord] = []
    step_log: list[dict[str, Any]] = []
    final: Done | None = None
    status: Status = "max_steps"
    error: str | None = None
    tokens_in = tokens_out = invalid = blocks = 0
    t_start = time.perf_counter()

    tracer.log(
        "episode_start",
        task_id=task.id,
        repeat=repeat,
        policy=policy.name,
        goal=task.goal,
        max_steps=guard.max_steps,
    )

    # The harness (not the agent) opens the start URL - it is part of the task definition.
    start = await session.execute(Goto(url=task.start_url))
    if not start.ok:
        status, error = "error", f"could not open start URL: {start.error}"

    step = 0
    while status == "max_steps" and error is None:
        step += 1
        try:
            guard.check_step(step)
        except GuardrailViolation:
            step -= 1
            break
        shot: Path | None = None
        if screenshots_dir is not None:
            shot = screenshots_dir / f"r{repeat}_step{step:02d}.png"
        t_obs = time.perf_counter()
        obs = await session.observe(screenshot_path=shot)
        observe_ms = (time.perf_counter() - t_obs) * 1e3

        ctx = PolicyContext(task, obs, history, step, guard.max_steps)
        try:
            decision = await policy.decide(ctx)
        except (PolicyError, ProviderError) as exc:
            status, error = "policy_error", str(exc)
            tracer.log("policy_error", step=step, error=str(exc))
            break
        tokens_in += decision.prompt_tokens
        tokens_out += decision.completion_tokens
        invalid += decision.invalid_attempts

        t_act = time.perf_counter()
        result: ActionResult = await session.execute(decision.action)
        act_ms = (time.perf_counter() - t_act) * 1e3
        if result.blocked_by:
            blocks += 1

        history.append(StepRecord(step, decision.action, decision.thought, result))
        shot_rel = None
        if shot is not None:
            shot_rel = str(shot.relative_to(screenshot_rel_to)) if screenshot_rel_to else str(shot)
        record = tracer.log(
            "step",
            task_id=task.id,
            repeat=repeat,
            step=step,
            url=obs.snapshot.url,
            observation=obs.snapshot.summary(),
            console_errors=obs.console_errors,
            thought=decision.thought,
            action=result.logged_action,
            result={
                "ok": result.ok,
                "message": result.message,
                "error": result.error,
                "blocked_by": result.blocked_by,
                "effect": result.effect,
                "assertion": result.assertion,
            },
            invalid_attempts=decision.invalid_attempts,
            invalid_errors=decision.invalid_errors,
            latency_ms={
                "observe": round(observe_ms, 1),
                "policy": round(decision.latency_ms, 1),
                "act": round(act_ms, 1),
            },
            tokens={"prompt": decision.prompt_tokens, "completion": decision.completion_tokens},
            screenshot=shot_rel,
        )
        step_log.append(record)

        if isinstance(decision.action, Done):
            final = decision.action
            status = "completed"

    checks = await evaluate_all(task.success, session.page, final)
    passed = status == "completed" and all(c.passed for c in checks)
    duration = time.perf_counter() - t_start
    result_obj = EpisodeResult(
        task_id=task.id,
        task_title=task.title,
        kind=task.kind,
        repeat=repeat,
        policy=policy.name,
        status=status,
        passed=passed,
        steps=step,
        duration_s=round(duration, 3),
        agent_success=final.success if final else None,
        agent_summary=final.summary if final else "",
        checks=checks,
        failure_reason=None if passed else _failure_reason(status, checks, error),
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        invalid_actions=invalid,
        guardrail_blocks=blocks,
        trace_path=str(tracer.path),
        step_log=step_log,
    )
    tracer.log(
        "episode_end",
        task_id=task.id,
        repeat=repeat,
        status=status,
        passed=passed,
        steps=step,
        duration_s=result_obj.duration_s,
        agent_success=result_obj.agent_success,
        agent_summary=result_obj.agent_summary,
        checks=[c.to_dict() for c in checks],
        failure_reason=result_obj.failure_reason,
        blocked_requests=list(guard.blocked_requests),
    )
    return result_obj
