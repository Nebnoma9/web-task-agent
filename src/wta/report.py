"""Write summary.json, summary.md and a self-contained HTML run report."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from jinja2 import Environment, select_autoescape

from wta.runner import SuiteResult


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def markdown_table(suite: SuiteResult) -> str:
    o = suite.overall()
    lines = [
        f"Policy: `{suite.policy}` · run `{suite.run_id}` · {o['episodes']} episodes · "
        f"success {o['passed']}/{o['episodes']} ({_pct(o['success_rate'])}) · "
        f"p50 {o['p50_s']:.2f}s · p95 {o['p95_s']:.2f}s",
        "",
        "| Task | Kind | Success | Mean steps | p50 (s) | p95 (s) |",
        "|---|---|---|---|---|---|",
    ]
    for t in suite.tasks:
        lines.append(
            f"| `{t.task_id}` | {t.kind} | {t.successes}/{t.runs} ({_pct(t.success_rate)}) | "
            f"{t.mean_steps:g} | {t.p50_s:.2f} | {t.p95_s:.2f} |"
        )
    failures = [(t.task_id, f) for t in suite.tasks for f in t.failures]
    if failures:
        lines += ["", "Failures:", ""]
        lines += [f"- `{tid}` {f}" for tid, f in failures]
    return "\n".join(lines) + "\n"


def write_report(suite: SuiteResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(suite.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(markdown_table(suite), encoding="utf-8")

    template_src = (resources.files("wta") / "templates" / "report.html.j2").read_text("utf-8")
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters["pct"] = _pct
    env.filters["compact_json"] = lambda v: json.dumps(v, ensure_ascii=False)
    html = env.from_string(template_src).render(
        suite=suite, overall=suite.overall(), episodes=suite.episodes, tasks=suite.tasks
    )
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
