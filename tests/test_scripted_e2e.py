"""End-to-end: the scripted policy against the bundled sites in headless Chromium.

Besides the happy path, these tests prove the harness judges the *page state* rather than the
agent's claim, and that guardrails turn into failed episodes rather than silent successes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ROOT, TASKS_DIR
from wta.agent import EpisodeResult, run_episode
from wta.guardrails import Guardrails
from wta.policies import ScriptedPolicy
from wta.redact import Redactor
from wta.tasks import ScriptStep, Task, load_tasks, resolve_secrets
from wta.trace import Tracer, read_trace

pytestmark = pytest.mark.browser

TASK_IDS = [t.id for t in load_tasks(TASKS_DIR)]


async def _run(task: Task, make_session, tmp_path: Path) -> EpisodeResult:
    secrets = resolve_secrets(task, ROOT)
    guard = Guardrails(
        max_steps=task.max_steps, allow_test_payment_data=task.allow_test_payment_data
    )
    session = await make_session(guard, secrets)
    with Tracer(tmp_path / f"{task.id}.jsonl", Redactor(secrets, include_env=False)) as tr:
        return await run_episode(
            task, ScriptedPolicy(), session, tr, screenshots_dir=tmp_path / "shots"
        )


def test_suite_has_ten_tasks(all_tasks: list[Task]) -> None:
    assert len(all_tasks) == 10
    assert {t.kind for t in all_tasks} == {"automation", "qa"}


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_scripted_policy_solves_task(task_id: str, all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == task_id)
    ep = await _run(task, make_session, tmp_path)
    assert ep.passed, ep.failure_reason
    assert ep.status == "completed"
    assert all(c.passed for c in ep.checks)
    assert ep.steps <= task.max_steps
    # observability: one trace line per step plus start/end, one screenshot per step
    records = read_trace(Path(ep.trace_path))
    assert records[0]["event"] == "episode_start" and records[-1]["event"] == "episode_end"
    assert sum(r["event"] == "step" for r in records) == ep.steps
    assert len(list((tmp_path / "shots").glob("*.png"))) == ep.steps


async def test_regression_is_reported_as_failure(all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == "dashboard-export-regression")
    ep = await _run(task, make_session, tmp_path)
    assert ep.passed  # the harness expected the agent to find the bug ...
    assert ep.agent_success is False  # ... and it did
    assert "buildCsv" in ep.agent_summary


async def test_harness_ignores_false_success_claims(all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == "shop-find-kettle-under-price")
    liar = task.model_copy(
        update={
            "scripted_plan": [
                ScriptStep(type="done", success=True, summary="Opened Compact Travel Kettle")
            ]
        }
    )
    ep = await _run(liar, make_session, tmp_path)
    assert ep.agent_success is True
    assert not ep.passed
    assert "url_contains" in (ep.failure_reason or "")


async def test_wrong_qa_verdict_fails(all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == "dashboard-export-regression")
    naive = task.model_copy(
        update={
            "scripted_plan": [ScriptStep(type="done", success=True, summary="export looks fine")]
        }
    )
    ep = await _run(naive, make_session, tmp_path)
    assert not ep.passed
    assert "agent_verdict" in (ep.failure_reason or "")


async def test_max_steps_stops_the_episode(all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == "shop-checkout-test-card")
    short = task.model_copy(update={"max_steps": 3})
    ep = await _run(short, make_session, tmp_path)
    assert ep.status == "max_steps"
    assert ep.steps == 3
    assert not ep.passed
    assert "step budget" in (ep.failure_reason or "")


async def test_payment_guardrail_fails_checkout_without_opt_in(
    all_tasks, make_session, tmp_path
) -> None:
    task = next(t for t in all_tasks if t.id == "shop-checkout-test-card")
    no_opt_in = task.model_copy(update={"allow_test_payment_data": False})
    ep = await _run(no_opt_in, make_session, tmp_path)
    assert ep.guardrail_blocks == 3  # card number, expiry, CVC
    assert not ep.passed
    blocked = [r for r in read_trace(Path(ep.trace_path)) if r.get("result", {}).get("blocked_by")]
    assert {r["result"]["blocked_by"] for r in blocked} == {"payment"}


async def test_secrets_never_reach_the_trace(all_tasks, make_session, tmp_path) -> None:
    task = next(t for t in all_tasks if t.id == "support-login")
    password = resolve_secrets(task, ROOT)["support_password"]
    ep = await _run(task, make_session, tmp_path)
    assert ep.passed
    raw = read_trace(Path(ep.trace_path))
    assert password not in str(raw)
    assert "{{secret:support_password}}" in str(raw)  # the placeholder is what gets logged
    assert password not in task.goal
