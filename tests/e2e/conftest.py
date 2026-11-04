"""E2E harness fixtures (SPEC §18.3 items 22-27). Standalone: imports nothing from
tests/unit.

Ordinary tests use a real ``panels serve`` subprocess on an OS-assigned port. The
``server`` fixture starts a fresh generation before every test so canonical rows, derived
state, and process-local publication registrations share the same isolated lifetime.
Tests whose subject is process ownership or special configuration keep using the
function-scoped ``server_factory`` path. Browser contexts come from pytest-playwright's
session ``browser``; the ``open_page`` / ``cli`` / ``api`` helpers drive the surfaces.
Every Playwright wait carries an explicit ``timeout``; the only sleep is the worker-step
readiness poll's 0.1s interval, which polls a condition inside a boot budget.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)
from tests.e2e.harness import (
    BOOT_BUDGET_S,
    FAKE_NOW,
    PLAN_BIN,
    REPO_ROOT,
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
)
from tests.support.server_fixtures import (  # noqa: F401 - fixture registration
    _reusable_server,
    api,
    cli,
    restart_server,
    server_factory,
    stop_server,
)
from tests.support.server_fixtures import (
    server as server,
)

__all__ = [
    "BOOT_BUDGET_S",
    "FAKE_NOW",
    "PLAN_BIN",
    "REPO_ROOT",
    "WAIT_MS",
    "ApiHelper",
    "JsonObject",
    "ServerHandle",
]


BROWSER_CHECK_FAILED = pytest.StashKey[bool]()


# Capture Linux's browser-cache location during collection, before test setup
# replaces HOME. Honor a caller's explicit Playwright override, including 0.
_BROWSER_CACHE = os.environ.get(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"),
)


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    # The driver snapshots its environment at startup; launch args are too late.
    # Keep the private HOME and restore this process's environment immediately.
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("PLAYWRIGHT_BROWSERS_PATH", _BROWSER_CACHE)
        driver = sync_playwright().start()
    try:
        yield driver
    finally:
        driver.stop()


@pytest.fixture
def context_factory(
    browser: Browser, request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Callable[[], BrowserContext]]:
    contexts: list[BrowserContext] = []

    def make() -> BrowserContext:
        ctx = browser.new_context()
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        contexts.append(ctx)
        return ctx

    yield make

    failed = request.node.stash.get(BROWSER_CHECK_FAILED, False)
    for index, ctx in enumerate(contexts):
        if failed:
            folder = Path(
                os.environ.get("PANELS_E2E_FAILURE_DIR", str(tmp_path / "browser-failure"))
            )
            folder = folder / re.sub(r"[^a-zA-Z0-9_.-]", "_", request.node.nodeid)
            folder.mkdir(parents=True, exist_ok=True)
            for page_index, page in enumerate(ctx.pages):
                stem = folder / f"context-{index}-page-{page_index}"
                try:
                    page.screenshot(
                        path=str(stem.with_suffix(".png")), full_page=True, timeout=5000
                    )
                    stem.with_suffix(".html").write_text(page.content())
                except Exception as error:
                    stem.with_suffix(".capture-error.txt").write_text(str(error))
            ctx.tracing.stop(path=str(folder / f"context-{index}-trace.zip"))
        else:
            ctx.tracing.stop()
        # A route handler can still await route.fetch() after the test's last visible
        # assertion. Wait for those handlers before closing their context, or Playwright
        # can dispose the response while the next test creates its context.
        if ctx.pages:
            ctx.unroute_all(behavior="wait")
        ctx.close()


@pytest.fixture
def open_page() -> Callable[..., Page]:
    def _open(
        ctx: BrowserContext,
        server: ServerHandle,
        route: str,
        ready_selector: str,
    ) -> Page:
        page = ctx.new_page()
        page.goto(server.base + "/" + route)
        page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        # Change-stream gate: wait until the on-open invalidation finishes, so the
        # requested target comes from the reconciled DOM rather than the first read.
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.sseReconciliations >= 1",
            timeout=WAIT_MS,
        )
        page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        return page

    return _open


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, Any, None]:
    outcome = yield
    report = outcome.get_result()
    if report.when in {"setup", "call"} and report.failed:
        item.stash[BROWSER_CHECK_FAILED] = True
