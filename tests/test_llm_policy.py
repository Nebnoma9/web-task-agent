"""LLMPolicy with a fake provider: re-asking on invalid output, prompt hygiene, and a full
browser episode driven through the LLM code path (no network, no API key)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import ROOT
from wta.actions import Click, Done
from wta.agent import run_episode
from wta.browser import Observation
from wta.guardrails import Guardrails
from wta.policies import LLMPolicy, PolicyContext, PolicyError
from wta.providers.base import ChatMessage, ChatProvider, Completion
from wta.redact import Redactor
from wta.snapshot import Snapshot, SnapshotNode
from wta.tasks import Task, resolve_secrets
from wta.trace import Tracer


class FakeProvider(ChatProvider):
    """Returns canned replies, or computes them from the prompt with a callback."""

    name = "fake"

    def __init__(self, replies=None, fn=None, vision: bool = True) -> None:
        super().__init__(model="fake-1", supports_vision=vision)
        self.replies = list(replies or [])
        self.fn = fn
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages, *, temperature=0.0, max_tokens=600):
        self.calls.append(messages)
        text = self.fn(messages) if self.fn else self.replies.pop(0)
        return Completion(text=text, model=self.model, prompt_tokens=100, completion_tokens=20)

    def _url(self) -> str:  # pragma: no cover - unused
        return ""

    def _headers(self) -> dict[str, str]:  # pragma: no cover
        return {}

    def _payload(self, messages, *, temperature, max_tokens):  # pragma: no cover
        return {}

    def _parse(self, data):  # pragma: no cover
        return Completion(text="", model="")


def _ctx(task: Task, png: bytes | None = None) -> PolicyContext:
    snap = Snapshot(
        url="http://127.0.0.1/x",
        title="X",
        nodes=[SnapshotNode(ref="e1", role="button", name="Export CSV")],
    )
    return PolicyContext(task, Observation(snapshot=snap, screenshot_png=png), [], 1, 8)


@pytest.fixture
def export_task(all_tasks: list[Task]) -> Task:
    return next(t for t in all_tasks if t.id == "dashboard-export-regression")


async def test_invalid_output_is_rejected_and_reasked(export_task: Task) -> None:
    prov = FakeProvider(
        replies=[
            "I think I should click the export button.",
            '{"action": {"type": "click", "ref": "export"}}',
            '{"thought": "click export", "action": {"type": "click", "ref": "e1"}}',
        ]
    )
    d = await LLMPolicy(prov).decide(_ctx(export_task))
    assert d.action == Click(ref="e1")
    assert d.invalid_attempts == 2
    assert d.prompt_tokens == 300 and d.completion_tokens == 60
    # The validation error is fed back verbatim on the retry.
    last_user = prov.calls[-1][-1].text
    assert "rejected" in last_user and "ref" in last_user


async def test_gives_up_after_max_invalid(export_task: Task) -> None:
    prov = FakeProvider(replies=["nope"] * 4)
    with pytest.raises(PolicyError):
        await LLMPolicy(prov, max_invalid=3).decide(_ctx(export_task))
    assert len(prov.calls) == 4


async def test_screenshot_only_sent_to_vision_models(export_task: Task) -> None:
    reply = '{"action": {"type": "done", "success": false, "summary": "x"}}'
    vision = FakeProvider(replies=[reply], vision=True)
    await LLMPolicy(vision).decide(_ctx(export_task, png=b"\x89PNG"))
    assert vision.calls[0][1].images == [b"\x89PNG"]

    blind = FakeProvider(replies=[reply], vision=False)
    policy = LLMPolicy(blind)
    assert not policy.wants_screenshot
    await policy.decide(_ctx(export_task, png=b"\x89PNG"))
    assert blind.calls[0][1].images == []


async def test_prompt_contains_snapshot_and_no_secret_values(all_tasks: list[Task]) -> None:
    task = next(t for t in all_tasks if t.id == "support-login")
    password = resolve_secrets(task, ROOT)["support_password"]
    prov = FakeProvider(replies=['{"type": "done", "success": false, "summary": "x"}'])
    await LLMPolicy(prov, redactor=Redactor(resolve_secrets(task, ROOT))).decide(_ctx(task))
    prompt = "\n".join(m.text for m in prov.calls[0])
    assert '[e1] button "Export CSV"' in prompt
    assert "{{secret:support_password}}" in prompt
    assert password not in prompt


@pytest.mark.browser
async def test_llm_code_path_end_to_end(export_task: Task, make_session, tmp_path: Path) -> None:
    """A rule-following fake 'model' reads the rendered snapshot, clicks Export, then reports."""

    def brain(messages: list[ChatMessage]) -> str:
        prompt = messages[-1].text
        if "PREVIOUS ACTIONS:\n(no previous actions)" in prompt:
            ref = re.search(r'\[(e\d+)\] button "Export CSV"', prompt)
            assert ref, prompt
            action = {"type": "click", "ref": ref.group(1)}
            return json.dumps({"thought": "try export", "action": action})
        assert "no_visible_change" in prompt and "buildCsv" in prompt
        return (
            '{"thought": "nothing happened and a TypeError was logged", "action": {"type": "done",'
            ' "success": false, "summary": "Export CSV click had no visible effect; console shows'
            ' TypeError buildCsv is not a function."}}'
        )

    session = await make_session(Guardrails(max_steps=export_task.max_steps))
    policy = LLMPolicy(FakeProvider(fn=brain))
    with Tracer(tmp_path / "t.jsonl", Redactor(include_env=False)) as tr:
        ep = await run_episode(export_task, policy, session, tr)
    assert ep.passed, ep.failure_reason
    assert ep.steps == 2
    assert ep.prompt_tokens == 200
    assert isinstance(Done(success=False, summary="x"), Done)
