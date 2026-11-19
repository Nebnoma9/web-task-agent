"""Command-line interface: ``wta run|list|validate|serve``."""

from __future__ import annotations

import asyncio
import os
import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from wta.agent import EpisodeResult
from wta.policies import LLMPolicy, ScriptedPolicy
from wta.policies.base import Policy
from wta.providers import PROVIDERS, ProviderError, make_provider
from wta.report import write_report
from wta.server import DEFAULT_SITES_DIR, serve_sites
from wta.tasks import load_tasks

app = typer.Typer(add_completion=False, no_args_is_help=True, help="web-task-agent harness")
console = Console()


class PolicyName(StrEnum):
    scripted = "scripted"
    llm = "llm"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (KEY=VALUE lines). Never overrides variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _print_episode(e: EpisodeResult) -> None:
    mark = "[green]PASS[/]" if e.passed else "[red]FAIL[/]"
    console.print(
        f"{mark} {e.task_id} r{e.repeat}  steps={e.steps}  {e.duration_s:.2f}s  status={e.status}"
        + (f"\n     [dim]{e.failure_reason}[/]" if e.failure_reason else "")
    )


@app.command()
def run(
    tasks_path: Annotated[Path, typer.Argument(help="Task YAML file or directory")] = Path("tasks"),
    policy: Annotated[PolicyName, typer.Option(help="scripted (no API key) or llm")] = (
        PolicyName.scripted
    ),
    provider: Annotated[str, typer.Option(help=f"LLM provider: {', '.join(PROVIDERS)}")] = "openai",
    model: Annotated[str | None, typer.Option(help="Model name (default per provider)")] = None,
    report: Annotated[Path, typer.Option(help="Output directory for report/traces")] = Path("out"),
    repeat: Annotated[int, typer.Option(min=1, max=50, help="Runs per task")] = 1,
    task: Annotated[list[str] | None, typer.Option(help="Only run these task ids")] = None,
    vision: Annotated[bool, typer.Option(help="Send screenshots to vision models")] = True,
    screenshots: Annotated[bool, typer.Option(help="Save a screenshot per step")] = True,
    headed: Annotated[bool, typer.Option(help="Show the browser window")] = False,
    max_steps: Annotated[int | None, typer.Option(help="Override per-task step budget")] = None,
    min_success: Annotated[
        float, typer.Option(help="Exit non-zero if success rate is below this (0-1)")
    ] = 0.0,
) -> None:
    """Run a task suite and write summary.json, summary.md, report.html and JSONL traces."""
    load_dotenv()
    tasks = load_tasks(tasks_path, only=task)

    def factory() -> Policy:
        if policy is PolicyName.scripted:
            return ScriptedPolicy()
        try:
            prov = make_provider(provider, model=model)
        except ProviderError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(2) from exc
        return LLMPolicy(prov, use_vision=vision)

    from wta.runner import run_suite  # imported lazily: keeps `wta list` fast

    console.print(f"Running {len(tasks)} task(s) x {repeat} with policy [bold]{policy.value}[/]")
    t0 = time.perf_counter()
    suite = asyncio.run(
        run_suite(
            tasks,
            factory,
            out_dir=report,
            repeat=repeat,
            headless=not headed,
            screenshots=screenshots,
            max_steps_override=max_steps,
            on_episode=_print_episode,
        )
    )
    path = write_report(suite, report)

    table = Table(title=f"Results ({suite.policy})")
    for col in ("task", "kind", "success", "mean steps", "p50 s", "p95 s"):
        table.add_column(col, justify="right" if col not in ("task", "kind") else "left")
    for t in suite.tasks:
        style = "green" if t.successes == t.runs else "red"
        table.add_row(
            t.task_id,
            t.kind,
            f"[{style}]{t.successes}/{t.runs}[/]",
            f"{t.mean_steps:g}",
            f"{t.p50_s:.2f}",
            f"{t.p95_s:.2f}",
        )
    console.print(table)
    o = suite.overall()
    console.print(
        f"Success rate: [bold]{o['passed']}/{o['episodes']} ({o['success_rate'] * 100:.0f}%)[/]  "
        f"p50={o['p50_s']:.2f}s p95={o['p95_s']:.2f}s  tokens in/out={o['prompt_tokens']}/"
        f"{o['completion_tokens']}  invalid={o['invalid_actions']}  "
        f"blocked={o['guardrail_blocks']}  wall={time.perf_counter() - t0:.1f}s"
    )
    console.print(f"Report: {path}")
    if suite.success_rate < min_success:
        console.print(f"[red]success rate below --min-success {min_success}[/]")
        raise typer.Exit(1)


@app.command("list")
def list_tasks(tasks_path: Annotated[Path, typer.Argument()] = Path("tasks")) -> None:
    """List tasks in a suite."""
    table = Table()
    for col in ("id", "kind", "max steps", "title"):
        table.add_column(col)
    for t in load_tasks(tasks_path):
        table.add_row(t.id, t.kind, str(t.max_steps), t.title)
    console.print(table)


@app.command()
def validate(tasks_path: Annotated[Path, typer.Argument()] = Path("tasks")) -> None:
    """Validate task YAML files against the schema."""
    tasks = load_tasks(tasks_path)
    console.print(f"[green]{len(tasks)} task(s) valid[/]")


@app.command()
def serve(
    port: Annotated[int, typer.Option()] = 8765,
    sites: Annotated[Path, typer.Option()] = DEFAULT_SITES_DIR,
) -> None:
    """Serve the demo sites for manual exploration (Ctrl+C to stop)."""
    with serve_sites(sites, port=port) as url:
        console.print(f"Serving {sites} at {url}/  (shop: {url}/shop/index.html)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":  # pragma: no cover
    app()
