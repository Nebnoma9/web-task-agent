"""Suite runner: serves the demo sites, runs every task N times, aggregates metrics."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from wta.agent import EpisodeResult, run_episode
from wta.browser import BrowserSession, launch_browser
from wta.guardrails import DEFAULT_ALLOWED_DOMAINS, Guardrails
from wta.policies.base import Policy
from wta.redact import Redactor
from wta.server import DEFAULT_SITES_DIR, serve_sites
from wta.tasks import Task, resolve_secrets
from wta.trace import Tracer

REPO_ROOT = Path(__file__).resolve().parents[2]


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 100]); 0.0 for an empty list."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass(slots=True)
class TaskStats:
    task_id: str
    title: str
    kind: str
    runs: int
    successes: int
    success_rate: float
    mean_steps: float
    p50_s: float
    p95_s: float
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SuiteResult:
    run_id: str
    policy: str
    started_at: str
    episodes: list[EpisodeResult] = field(default_factory=list)
    tasks: list[TaskStats] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return sum(e.passed for e in self.episodes) / len(self.episodes) if self.episodes else 0.0

    def overall(self) -> dict[str, Any]:
        durations = [e.duration_s for e in self.episodes]
        return {
            "episodes": len(self.episodes),
            "passed": sum(e.passed for e in self.episodes),
            "success_rate": round(self.success_rate, 4),
            "mean_steps": round(
                sum(e.steps for e in self.episodes) / max(len(self.episodes), 1), 2
            ),
            "p50_s": round(percentile(durations, 50), 3),
            "p95_s": round(percentile(durations, 95), 3),
            "prompt_tokens": sum(e.prompt_tokens for e in self.episodes),
            "completion_tokens": sum(e.completion_tokens for e in self.episodes),
            "invalid_actions": sum(e.invalid_actions for e in self.episodes),
            "guardrail_blocks": sum(e.guardrail_blocks for e in self.episodes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "policy": self.policy,
            "started_at": self.started_at,
            "overall": self.overall(),
            "tasks": [t.to_dict() for t in self.tasks],
            "episodes": [
                {k: v for k, v in e.to_dict().items() if k != "step_log"} for e in self.episodes
            ],
        }


def aggregate(tasks: list[Task], episodes: list[EpisodeResult]) -> list[TaskStats]:
    out = []
    for t in tasks:
        eps = [e for e in episodes if e.task_id == t.id]
        if not eps:
            continue
        durs = [e.duration_s for e in eps]
        ok = sum(e.passed for e in eps)
        out.append(
            TaskStats(
                task_id=t.id,
                title=t.title,
                kind=t.kind,
                runs=len(eps),
                successes=ok,
                success_rate=round(ok / len(eps), 4),
                mean_steps=round(sum(e.steps for e in eps) / len(eps), 2),
                p50_s=round(percentile(durs, 50), 3),
                p95_s=round(percentile(durs, 95), 3),
                failures=[f"r{e.repeat}: {e.failure_reason}" for e in eps if not e.passed],
            )
        )
    return out


async def run_suite(
    tasks: list[Task],
    policy_factory: Callable[[], Policy],
    *,
    out_dir: Path,
    repeat: int = 1,
    headless: bool = True,
    screenshots: bool = True,
    sites_dir: Path = DEFAULT_SITES_DIR,
    max_steps_override: int | None = None,
    on_episode: Callable[[EpisodeResult], None] | None = None,
) -> SuiteResult:
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - one-off, before any I/O loop
    started = dt.datetime.now(dt.UTC)
    policy = policy_factory()
    suite = SuiteResult(
        run_id=started.strftime("%Y%m%dT%H%M%SZ"),
        policy=policy.name,
        started_at=started.isoformat(timespec="seconds"),
    )
    with serve_sites(sites_dir) as base_url:
        async with async_playwright() as pw:
            browser = await launch_browser(pw, headless=headless)
            try:
                for task in tasks:
                    secrets = resolve_secrets(task, REPO_ROOT)
                    redactor = Redactor(secrets)
                    for r in range(1, repeat + 1):
                        guard = Guardrails(
                            allowed_domains=tuple(task.allowed_domains or DEFAULT_ALLOWED_DOMAINS),
                            max_steps=max_steps_override or task.max_steps,
                            allow_test_payment_data=task.allow_test_payment_data,
                        )
                        session = await BrowserSession.create(
                            browser, guard=guard, base_url=base_url, secrets=secrets
                        )
                        shots = out_dir / "screenshots" / task.id if screenshots else None
                        with Tracer(out_dir / "traces" / f"{task.id}__r{r}.jsonl", redactor) as tr:
                            try:
                                ep = await run_episode(
                                    task,
                                    policy,
                                    session,
                                    tr,
                                    repeat=r,
                                    screenshots_dir=shots,
                                    screenshot_rel_to=out_dir,
                                )
                            finally:
                                await session.close()
                        ep.trace_path = str(Path(ep.trace_path).relative_to(out_dir))
                        suite.episodes.append(ep)
                        if on_episode:
                            on_episode(ep)
            finally:
                await browser.close()
    suite.tasks = aggregate(tasks, suite.episodes)
    return suite
