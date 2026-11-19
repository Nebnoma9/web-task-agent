from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, async_playwright

from wta.browser import BrowserSession, launch_browser
from wta.guardrails import Guardrails
from wta.server import serve_sites
from wta.tasks import Task, load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    with serve_sites() as url:
        yield url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        b = await launch_browser(pw)
        yield b
        await b.close()


@pytest.fixture(scope="session")
def all_tasks() -> list[Task]:
    return load_tasks(TASKS_DIR)


@pytest_asyncio.fixture(loop_scope="session")
async def make_session(browser: Browser, base_url: str):
    sessions: list[BrowserSession] = []

    async def _make(guard: Guardrails | None = None, secrets: dict[str, str] | None = None):
        s = await BrowserSession.create(
            browser, guard=guard or Guardrails(), base_url=base_url, secrets=secrets
        )
        sessions.append(s)
        return s

    yield _make
    for s in sessions:
        await s.close()
