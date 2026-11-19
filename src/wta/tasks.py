"""Task definitions loaded from ``tasks/*.yaml``.

A task couples a natural-language goal (what the agent sees) with success criteria that the
harness checks against the real page state after the run - independently of whatever the agent
claims in its final ``done`` action.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------------------------
# Success criteria (checked by the harness, see wta.checks)
# ---------------------------------------------------------------------------------------------


class _Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""


class UrlContains(_Criterion):
    type: Literal["url_contains"]
    value: str


class TextVisible(_Criterion):
    type: Literal["text_visible"]
    text: str


class TextAbsent(_Criterion):
    type: Literal["text_absent"]
    text: str


class SelectorText(_Criterion):
    type: Literal["selector_text"]
    selector: str
    contains: str


class LocalStorage(_Criterion):
    """Inspect a JSON value in localStorage. ``path`` is a dotted path (list indices allowed)."""

    type: Literal["local_storage"]
    key: str
    path: str = ""
    equals: Any = None
    contains: str | None = None
    exists: bool = True


class AgentVerdict(_Criterion):
    """For QA tasks: the agent's ``done.success`` must match the known ground truth."""

    type: Literal["agent_verdict"]
    expected: bool


class SummaryMentions(_Criterion):
    """The agent's final summary must mention all keywords (case-insensitive)."""

    type: Literal["summary_mentions"]
    keywords: list[str] = Field(min_length=1)


Criterion = Annotated[
    UrlContains
    | TextVisible
    | TextAbsent
    | SelectorText
    | LocalStorage
    | AgentVerdict
    | SummaryMentions,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------------------------
# Scripted reference plan (used by the deterministic `scripted` policy)
# ---------------------------------------------------------------------------------------------


class Target(BaseModel):
    """Locates a snapshot node by role and accessible name - resolved to a ref at run time."""

    model_config = ConfigDict(extra="forbid")

    role: str
    name: str | None = None
    name_contains: str | None = None
    nth: int = 0


class ScriptStep(BaseModel):
    """One step of a reference plan. Same fields as an action, but ``target`` instead of ``ref``.

    The special type ``done_from_checks`` finishes with ``success`` = "every assert_text /
    wait_for so far passed", which is how the scripted policy answers QA tasks honestly.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    target: Target | None = None
    url: str | None = None
    text: str | None = None
    option: str | None = None
    key: str | None = None
    direction: Literal["up", "down"] | None = None
    amount: int | None = None
    timeout_ms: int | None = None
    present: bool | None = None
    success: bool | None = None
    summary: str | None = None


class SecretRef(BaseModel):
    """A secret read from a fixture JSON file.

    ``path`` is a dotted path into the JSON document, e.g. ``users.0.password``.
    """

    model_config = ConfigDict(extra="forbid")

    fixture: str
    path: str


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    title: str
    kind: Literal["automation", "qa"] = "automation"
    start_url: str
    goal: str
    max_steps: int = Field(default=15, ge=1, le=100)
    allow_test_payment_data: bool = False
    allowed_domains: list[str] | None = None
    secrets: dict[str, SecretRef] = Field(default_factory=dict)
    success: list[Criterion] = Field(min_length=1)
    scripted_plan: list[ScriptStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_path: str | None = None

    @model_validator(mode="after")
    def _qa_needs_verdict(self) -> Task:
        if self.kind == "qa" and not any(isinstance(c, AgentVerdict) for c in self.success):
            raise ValueError("qa tasks must include an agent_verdict criterion")
        return self


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def resolve_secrets(task: Task, root: Path) -> dict[str, str]:
    """Load the secret values a task references. Values never enter prompts or traces."""
    out: dict[str, str] = {}
    for name, ref in task.secrets.items():
        data = json.loads((root / ref.fixture).read_text(encoding="utf-8"))
        out[name] = str(_dig(data, ref.path))
    return out


def load_task(path: Path) -> Task:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    data["source_path"] = str(path)
    return Task.model_validate(data)


def load_tasks(path: Path, only: list[str] | None = None) -> list[Task]:
    """Load one YAML file or every ``*.yaml`` in a directory (sorted by filename)."""
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    tasks = [load_task(f) for f in files]
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate task ids: {sorted(dupes)}")
    if only:
        wanted = set(only)
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            raise ValueError(f"unknown task ids: {sorted(missing)}")
    return tasks
