"""Pydantic-validated action schema.

The model (or the scripted policy) must emit exactly one action per step as JSON, e.g.::

    {"thought": "The search box is e3", "action": {"type": "fill", "ref": "e3", "text": "kettle"}}

Anything that does not validate is rejected with a precise error message that is fed back to
the policy so it can correct itself (see :class:`wta.policies.llm.LLMPolicy`).
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

REF_PATTERN = r"^e\d{1,5}$"

# Keys we allow for `press`. A closed set keeps the action space small and auditable.
ALLOWED_KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Escape",
        "Backspace",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        "Space",
    }
)


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Goto(_Action):
    """Navigate to a URL (absolute, or a path relative to the task's base URL)."""

    type: Literal["goto"] = "goto"
    url: str = Field(min_length=1, max_length=2048)


class Click(_Action):
    """Click the element with the given snapshot ref."""

    type: Literal["click"] = "click"
    ref: str = Field(pattern=REF_PATTERN)


class Fill(_Action):
    """Replace the value of a text input / textarea. `{{secret:name}}` placeholders are resolved
    at execution time and never shown to the model or written to logs."""

    type: Literal["fill"] = "fill"
    ref: str = Field(pattern=REF_PATTERN)
    text: str = Field(max_length=2000)


class Select(_Action):
    """Choose an option in a <select> by visible label (or value)."""

    type: Literal["select"] = "select"
    ref: str = Field(pattern=REF_PATTERN)
    option: str = Field(min_length=1, max_length=200)


class Press(_Action):
    """Press a keyboard key, optionally focused on an element."""

    type: Literal["press"] = "press"
    key: str
    ref: str | None = Field(default=None, pattern=REF_PATTERN)

    @field_validator("key")
    @classmethod
    def _key_allowed(cls, v: str) -> str:
        if v not in ALLOWED_KEYS:
            raise ValueError(f"key {v!r} not allowed; use one of {sorted(ALLOWED_KEYS)}")
        return v


class Scroll(_Action):
    """Scroll the page, or scroll an element into view when `ref` is given."""

    type: Literal["scroll"] = "scroll"
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=600, ge=50, le=5000)
    ref: str | None = Field(default=None, pattern=REF_PATTERN)


class WaitFor(_Action):
    """Wait until `text` is visible on the page (bounded timeout)."""

    type: Literal["wait_for"] = "wait_for"
    text: str = Field(min_length=1, max_length=300)
    timeout_ms: int = Field(default=3000, ge=100, le=10_000)


class AssertText(_Action):
    """Check (without failing the run) that `text` is / is not visible. The outcome is recorded
    and returned to the policy - useful for QA-style tasks."""

    type: Literal["assert_text"] = "assert_text"
    text: str = Field(min_length=1, max_length=300)
    present: bool = True


class Done(_Action):
    """Finish the task. `success` is the agent's own claim; the harness verifies independently."""

    type: Literal["done"] = "done"
    success: bool
    summary: str = Field(min_length=1, max_length=2000)


Action = Annotated[
    Goto | Click | Fill | Select | Press | Scroll | WaitFor | AssertText | Done,
    Field(discriminator="type"),
]

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)

ACTION_TYPES: tuple[str, ...] = (
    "goto",
    "click",
    "fill",
    "select",
    "press",
    "scroll",
    "wait_for",
    "assert_text",
    "done",
)


class ActionParseError(ValueError):
    """Raised when policy output cannot be turned into a valid action."""


class PolicyOutput(BaseModel):
    """Envelope the LLM is asked to return."""

    model_config = ConfigDict(extra="ignore")

    thought: str = ""
    action: dict[str, Any]


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    m = _FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ActionParseError("no JSON object found in model output")
        candidate = candidate[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ActionParseError(f"invalid JSON: {exc.msg} at char {exc.pos}") from exc
    if not isinstance(obj, dict):
        raise ActionParseError("top-level JSON value must be an object")
    return obj


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(x) for x in err["loc"]) or "action"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def parse_action(raw: str | dict[str, Any]) -> tuple[Action, str]:
    """Parse model output into a validated action.

    Accepts either the envelope ``{"thought": ..., "action": {...}}`` or a bare action object,
    optionally wrapped in a Markdown code fence. Returns ``(action, thought)``.
    """
    obj = _extract_json_object(raw) if isinstance(raw, str) else raw
    thought = ""
    if "action" in obj and isinstance(obj["action"], dict):
        thought = str(obj.get("thought", ""))
        obj = obj["action"]
    if "type" not in obj:
        raise ActionParseError(f"action.type is required; one of {list(ACTION_TYPES)}")
    if obj["type"] not in ACTION_TYPES:
        raise ActionParseError(f"unknown action type {obj['type']!r}; one of {list(ACTION_TYPES)}")
    try:
        action = _ACTION_ADAPTER.validate_python(obj)
    except ValidationError as exc:
        raise ActionParseError(_format_validation_error(exc)) from exc
    return action, thought


def action_json_schema() -> dict[str, Any]:
    """JSON schema of the action union, embedded in the LLM system prompt."""
    return _ACTION_ADAPTER.json_schema()
