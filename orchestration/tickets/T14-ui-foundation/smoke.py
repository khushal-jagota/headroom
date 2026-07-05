#!/usr/bin/env python3
"""T14 UI-foundation self-smoke. Boots an in-process test-mode server (uvicorn on a
daemon thread, temp DB, fake clock) and drives real headless chromium (Playwright) to
prove the shell references and their order, the meta keys the JS consumes, the default
route + placeholders wired through the router, and the WS event-append -> one-debounced
-flush invariant (one event => exactly one flush; two rapid events => still one flush).
Straight-line, assert-based, numbered `ok NN` prints; any failed assertion raises ->
traceback + non-zero exit; the try/finally always stops the server, closes the browser,
and removes the temp dir. Exits 0 with a final SMOKE PASS line on success. Not under
tests/ — never touched by pytest. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T14-ui-foundation/smoke.py
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

REPO = Path(__file__).resolve().parents[3]
FAKE_NOW = "2026-07-04T12:00:00"
PORT = 8790

ASSET_REFS = [
    "/assets/tokens.css",
    "/assets/app.css",
    "/assets/config.js",
    "/assets/api.js",
    "/assets/markdown.js",
    "/assets/components.js",
    "/assets/app.js",
]

_CHECK = 0


def ok(msg: str) -> None:
    global _CHECK
    _CHECK += 1
    print(f"ok {_CHECK:02d} — {msg}")


def free_or(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback:
        fallback.bind(("127.0.0.1", 0))
        return int(fallback.getsockname()[1])


def wait_ready(base: str, budget: float = 15.0, interval: float = 0.25) -> None:
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/meta", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    raise AssertionError("server did not become ready within budget")


def main() -> int:
    os.chdir(REPO)  # create_app mounts StaticFiles(directory="assets") cwd-relative
    tmp = Path(tempfile.mkdtemp(prefix="t14-smoke-"))
    port = free_or(PORT)
    base = f"http://127.0.0.1:{port}"
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_FAKE_NOW": FAKE_NOW,
        "PLAN_DB_PATH": str(tmp / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(tmp / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
        "PLAN_WS_POLL_MS": "50",  # tighten batch cadence; ui_debounce_ms stays 250
    }
    config = load_config(path=None, env=env)

    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
        create_schema(bootstrap)
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path, config.db_busy_timeout_ms)

    app = create_app(config, clock, adapters, conn_factory)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)

    playwright = None
    browser = None
    page = None
    client: httpx.Client | None = None
    try:
        thread.start()
        wait_ready(base)
        ok(f"in-process server up on {port}; /api/meta 200")

        client = httpx.Client(base_url=base, timeout=10.0)

        # (a) Shell references present, in dependency order; every asset served.
        r = client.get("/")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html"), r.headers
        body = r.text
        positions = [body.index(ref) for ref in ASSET_REFS]  # index raises if absent
        assert positions == sorted(positions), positions
        assert len(set(positions)) == len(positions), positions
        for ref in ASSET_REFS:
            got = client.get(ref)
            assert got.status_code == 200, f"{ref} -> {got.status_code}"
        ok("(a) shell references present, strictly ordered; all 7 assets served")

        # (b) The exact meta keys the JS consumes.
        meta = client.get("/api/meta").json()
        assert meta == {"ui_debounce_ms": 250, "ws_poll_ms": 50, "test_mode": True}, meta
        ok("(b) /api/meta = ui_debounce_ms 250, ws_poll_ms 50, test_mode True")

        # Boot chromium and load the app.
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch()
        page = browser.new_page()
        console_lines: list[str] = []
        page.on("console", lambda m: console_lines.append(m.text))
        page.goto(base + "/")
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
            timeout=15000,
        )
        assert page.evaluate("location.hash") == "#/day", page.evaluate("location.hash")
        assert "not built yet" in page.inner_text("#app")
        ok("shell mounted; default route #/day; placeholder wired; WS open")

        # (c1) Single append -> exactly one debounced flush, and the registered
        # callback really re-rendered the active screen (codex impl-review
        # finding 2: the bus counter alone would pass even with no subscriber).
        # route() builds a fresh screen div per render, so a marker set on the
        # current one must be gone after the flush.
        time.sleep(0.5)
        page.evaluate("document.querySelector('.screen').setAttribute('data-smoke', '1')")
        f0 = page.evaluate("window.__plannerDebug.flushes")
        r = client.post("/api/ideas", json={"title": "smoke bus proof 1"})
        assert r.status_code == 200 and r.json()["id"].startswith("idea_"), r.text
        page.wait_for_function(
            "f => window.__plannerDebug.flushes === f + 1", arg=f0, timeout=5000
        )
        time.sleep(1.0)  # > poll 50ms + debounce 250ms + margin
        assert page.evaluate("window.__plannerDebug.flushes") == f0 + 1
        assert any("[planner] flush" in line for line in console_lines)
        remarked = page.evaluate("document.querySelector('.screen').hasAttribute('data-smoke')")
        assert remarked is False
        ok("(c1) one append -> one debounced flush; screen re-rendered; console hook observed")

        # (c2) Two rapid appends coalesce into one flush; cursor tracks to 3.
        f1 = page.evaluate("window.__plannerDebug.flushes")
        r2 = client.post("/api/ideas", json={"title": "smoke bus proof 2"})
        r3 = client.post("/api/ideas", json={"title": "smoke bus proof 3"})
        assert r2.status_code == 200 and r3.status_code == 200, (r2.text, r3.text)
        page.wait_for_function(
            "f => window.__plannerDebug.flushes === f + 1", arg=f1, timeout=5000
        )
        time.sleep(1.0)
        assert page.evaluate("window.__plannerDebug.flushes") == f1 + 1
        assert page.evaluate("window.__plannerDebug.cursor") == 3
        ok("(c2) two rapid appends -> one flush; cursor == 3")

        # Router navigation: every route shape renders its placeholder
        # (codex plan-review finding 5 — Day alone would leave five screens unproven).
        for route in ("#/review", "#/board", "#/sprint", "#/backlog", "#/ticket/t_x", "#/day"):
            page.goto(base + "/" + route)
            page.wait_for_function(
                "r => location.hash === r", arg=route, timeout=5000
            )
            assert "not built yet" in page.inner_text("#app"), route
        ok("router nav: all six route shapes render placeholders")

        print(f"SMOKE PASS ({_CHECK} checks)")
        return 0
    finally:
        if client is not None:
            client.close()
        if page is not None:
            page.close()
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        server.should_exit = True
        thread.join(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
