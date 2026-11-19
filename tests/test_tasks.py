from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import TASKS_DIR
from wta.actions import parse_action
from wta.tasks import Task, load_tasks


def test_all_tasks_validate_and_have_unique_ids() -> None:
    tasks = load_tasks(TASKS_DIR)
    assert len({t.id for t in tasks}) == len(tasks) == 10


def test_scripted_plans_only_use_known_step_types(all_tasks: list[Task]) -> None:
    for t in all_tasks:
        for step in t.scripted_plan:
            if step.type == "done_from_checks":
                continue
            payload = {
                k: v for k, v in step.model_dump(exclude={"target"}).items() if v is not None
            }
            if step.target is not None:
                payload["ref"] = "e1"
            parse_action(payload)  # raises if the plan step is not a valid action


def test_qa_tasks_require_verdict() -> None:
    with pytest.raises(ValidationError, match="agent_verdict"):
        Task.model_validate(
            {
                "id": "qa-no-verdict",
                "title": "x",
                "kind": "qa",
                "start_url": "/",
                "goal": "x",
                "success": [{"type": "text_visible", "text": "x"}],
            }
        )


def test_filter_and_unknown_ids(tmp_path: Path) -> None:
    assert [t.id for t in load_tasks(TASKS_DIR, only=["support-login"])] == ["support-login"]
    with pytest.raises(ValueError, match="unknown task ids"):
        load_tasks(TASKS_DIR, only=["nope"])


def test_unknown_criterion_rejected(tmp_path: Path) -> None:
    f = tmp_path / "t.yaml"
    f.write_text(
        "id: bad-task\ntitle: x\nstart_url: /\ngoal: x\nsuccess:\n  - {type: vibes_ok}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_tasks(f)
