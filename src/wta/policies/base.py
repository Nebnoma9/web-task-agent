"""Policy interface: given the task, the current observation and the history, choose one action."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from wta.actions import Action
from wta.browser import ActionResult, Observation
from wta.tasks import Task


@dataclass(slots=True)
class StepRecord:
    step: int
    action: Action
    thought: str
    result: ActionResult


@dataclass(slots=True)
class PolicyContext:
    task: Task
    observation: Observation
    history: list[StepRecord]
    step: int
    max_steps: int


@dataclass(slots=True)
class Decision:
    action: Action
    thought: str = ""
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    invalid_attempts: int = 0
    invalid_errors: list[str] = field(default_factory=list)


class PolicyError(RuntimeError):
    """The policy could not produce a valid action (e.g. repeated invalid JSON)."""


class Policy(Protocol):
    name: str

    @property
    def wants_screenshot(self) -> bool: ...

    def reset(self, task: Task) -> None: ...

    async def decide(self, ctx: PolicyContext) -> Decision: ...
