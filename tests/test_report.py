from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import TASKS_DIR
from wta.policies import ScriptedPolicy
from wta.report import markdown_table, write_report
from wta.runner import percentile, run_suite
from wta.tasks import load_tasks


def test_percentile() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([3.0], 95) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95) == pytest.approx(4.8)
    assert percentile([5.0, 1.0, 3.0], 0) == 1.0


@pytest.mark.browser
async def test_suite_run_writes_report(tmp_path: Path) -> None:
    tasks = load_tasks(
        TASKS_DIR, only=["support-wrong-password-rejected", "dashboard-export-regression"]
    )
    suite = await run_suite(tasks, ScriptedPolicy, out_dir=tmp_path, repeat=2)
    assert len(suite.episodes) == 4
    assert suite.success_rate == 1.0
    assert [t.runs for t in suite.tasks] == [2, 2]

    path = write_report(suite, tmp_path)
    html = path.read_text(encoding="utf-8")
    for t in tasks:
        assert t.id in html
    assert "validate the harness" in html  # scripted-policy disclaimer
    assert "no_visible_change" in html  # per-step verify outcome is shown
    assert "screenshots/dashboard-export-regression/r1_step01.png" in html

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["overall"]["episodes"] == 4
    assert summary["overall"]["success_rate"] == 1.0
    assert {t["task_id"] for t in summary["tasks"]} == {t.id for t in tasks}

    md = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert md == markdown_table(suite)
    assert "| `dashboard-export-regression` | qa | 2/2 (100%)" in md

    traces = sorted(p.name for p in (tmp_path / "traces").glob("*.jsonl"))
    assert len(traces) == 4
    assert (tmp_path / "screenshots" / "dashboard-export-regression" / "r2_step01.png").exists()
