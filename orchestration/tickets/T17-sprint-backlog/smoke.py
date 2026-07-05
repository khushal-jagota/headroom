#!/usr/bin/env python3
"""T17 Sprint + Backlog self-smoke. Boots an in-process test-mode server (uvicorn on a
daemon thread, temp DB seeded with the demo dataset, REAL clock) and drives real headless
chromium (Playwright) to prove SPEC §10 screens 5 and 6 on the T14 foundation: the sprint
kickoff fields render editable pre-freeze, items group by status with ticket rollups, loose
tickets link out, a frozen-write surfaces a structured Error line while addenda append still
works, and the backlog lists deferred items by server priority with working item/idea creates.

Amendments baked in (plan §10):
  A1  real clock — PLAN_TEST_MODE=1 but NO PLAN_FAKE_NOW, matching seed_demo's wall-clock
      anchoring; the demo sprint spans today-3 .. today+10 so /api/sprint/current always
      resolves it.
  A2  deterministic freeze window — PLAN_UI_DEBOUNCE_MS=1500 (a hard floor between any event
      append and the earliest re-render) and PLAN_WS_POLL_MS=250; asserted via /api/meta.
  A3  conditional script injection — GET "/" first; inject the two screen files via
      page.add_script_tag ONLY when the shared shell does not already reference them.

Straight-line, assert-based, numbered `ok NN` prints; any failed assertion raises ->
traceback + non-zero exit; the try/finally always stops the server, closes the browser, and
removes the temp dir. Exits 0 with a final SMOKE PASS line on success. Not under tests/ —
never touched by pytest. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T17-sprint-backlog/smoke.py
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
from planner.seed.demo import seed_demo

REPO = Path(__file__).resolve().parents[3]
PORT = 8793
WAIT = 8000  # ms — every wait_for_* budget (A2: >= 8000)

SCREEN_REFS = ["/assets/screens-sprint.js", "/assets/screens-backlog.js"]

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


def nav(page, h: str) -> None:
    # Navigate only by setting location.hash (A3): a page.goto reload would discard the
    # injected screen scripts. We start at #/day, so each hash change fires a real
    # hashchange -> route() -> the injected render.
    page.evaluate("h => { location.hash = h; }", h)


def main() -> int:
    os.chdir(REPO)  # create_app mounts StaticFiles(directory="assets") cwd-relative
    tmp = Path(tempfile.mkdtemp(prefix="t17-smoke-"))
    port = free_or(PORT)
    base = f"http://127.0.0.1:{port}"
    env = {
        "PLAN_TEST_MODE": "1",                    # A1: no PLAN_FAKE_NOW -> real clock
        "PLAN_DB_PATH": str(tmp / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(tmp / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
        "PLAN_UI_DEBOUNCE_MS": "1500",            # A2: hard floor before the re-render
        "PLAN_WS_POLL_MS": "250",
    }
    config = load_config(path=None, env=env)

    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
        create_schema(bootstrap)
    # seed_demo requires an empty DB -> seed before the server starts.
    with connect(config.db_path, config.db_busy_timeout_ms) as seed_conn:
        seed_demo(seed_conn)
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

        # A2: the env plumbing is asserted before any UI work.
        meta = client.get("/api/meta").json()
        assert meta["ui_debounce_ms"] == 1500, meta
        assert meta["ws_poll_ms"] == 250, meta
        ok("(A2) /api/meta ui_debounce_ms == 1500, ws_poll_ms == 250")

        # A3: decide shell mode from the served HTML.
        shell_html = client.get("/").text
        integrated = all(ref in shell_html for ref in SCREEN_REFS)

        # Boot chromium and load the app.
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base + "/")
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
            timeout=WAIT,
        )
        assert page.evaluate("location.hash") == "#/day", page.evaluate("location.hash")

        if integrated:
            print("shell mode: integrated")
        else:
            for ref in SCREEN_REFS:
                page.add_script_tag(url=base + ref)
            print("shell mode: injected (shell integration pending)")
        ok("app booted at #/day; WS open; screen scripts registered")

        # === Sprint screen ====================================================
        nav(page, "#/sprint")
        page.wait_for_selector('[data-screen="sprint"] [data-kickoff]', timeout=WAIT)

        # (s1) kickoff: four fields in fixed order, each with a markdown block + editor.
        fields = page.query_selector_all('[data-kickoff] [data-field]')
        assert len(fields) == 4, len(fields)
        labels = page.eval_on_selector_all(
            '[data-kickoff] .sprint-field-label', "els => els.map(e => e.textContent)"
        )
        assert labels == ["Limiting factor", "Primary bet", "Supports", "Premortem"], labels
        for key in ("limiting_factor", "primary_bet", "supports", "premortem"):
            assert page.query_selector(f'[data-field="{key}"] .markdown-block') is not None, key
            assert page.query_selector(f'[data-field="{key}"] .field-editor') is not None, key
        ok("(s1) kickoff: 4 fields, exact labels, markdown block + editor each (pre-freeze)")

        # (s2) items grouped by status with rollup counts.
        active = page.query_selector_all('[data-status-group="active"] [data-item-id]')
        assert len(active) == 1, len(active)
        active_title = page.eval_on_selector(
            '[data-status-group="active"] [data-item-id] .entity-row-title', "e => e.textContent"
        )
        assert active_title == "Ship the demo feature end to end.", active_title
        rollup = page.eval_on_selector(
            '[data-status-group="active"] [data-item-id] [data-rollup]', "e => e.textContent"
        )
        assert "2 tickets" in rollup, rollup
        assert "needs plan 1" in rollup, rollup
        assert "in progress 1" in rollup, rollup
        assert "Research the search index options." in page.inner_text('[data-status-group="todo"]')
        assert "Retire the legacy export job." in page.inner_text('[data-status-group="done"]')
        for empty_group in ("blocked", "deferred_next_sprint"):
            assert page.query_selector(f'[data-status-group="{empty_group}"]') is not None
            empty_rows = page.query_selector_all(
                f'[data-status-group="{empty_group}"] [data-item-id]'
            )
            assert empty_rows == [], (empty_group, len(empty_rows))
        ok("(s2) items grouped by status; active rollup shows '2 tickets' + per-state counts")

        # (s3) loose tickets: six ticket links, all hash-only to #/ticket/.
        loose = page.query_selector_all('[data-loose] [data-ticket-id]')
        assert len(loose) == 6, len(loose)
        hrefs = page.eval_on_selector_all(
            '[data-loose] [data-ticket-id]', "els => els.map(e => e.getAttribute('href'))"
        )
        assert all(h.startswith("#/ticket/") for h in hrefs), hrefs
        loose_titles = page.eval_on_selector_all(
            '[data-loose] [data-ticket-id] .entity-row-title', "els => els.map(e => e.textContent)"
        )
        assert "Fix the flaky login test." in loose_titles, loose_titles
        ok("(s3) loose: 6 ticket links, every href #/ticket/*, 'Fix the flaky login test.' present")

        # (s4) frozen-write surfaces a structured Error line.
        sid = client.get("/api/sprint/current").json()["sprint"]["id"]
        page.fill(
            '[data-field="limiting_factor"] .field-editor-input', "edited while about to freeze"
        )
        # No agent headers -> treated as human -> the reject_agents guard passes.
        fr = client.post(f"/api/sprints/{sid}/freeze-kickoff")
        assert fr.status_code == 200, fr.text
        # Immediately (well inside the 1500ms debounce floor before the re-render).
        page.click('[data-field="limiting_factor"] .field-editor-actions .button')
        page.wait_for_selector('[data-field="limiting_factor"] .error-line', timeout=WAIT)
        code = page.eval_on_selector(
            '[data-field="limiting_factor"] .error-line .error-code', "e => e.textContent"
        )
        assert code == "frozen_write", code
        ok("(s4) PATCH on frozen kickoff field surfaces error-line code 'frozen_write'")

        # (s5) after the freeze refetch: editors gone, frozen chip, no freeze button;
        # the addenda append form still works post-freeze.
        page.wait_for_function(
            "() => document.querySelectorAll('[data-kickoff] .field-editor').length === 0",
            timeout=WAIT,
        )
        assert page.get_attribute('[data-kickoff]', 'data-frozen') == "1"
        assert page.query_selector('[data-kickoff] .chip--frozen') is not None
        assert len(page.query_selector_all('[data-freeze="kickoff"]')) == 0
        assert page.query_selector('[data-addenda-form]') is not None
        page.fill('[data-addenda-form] [data-input="date"]', "2026-07-05")
        page.fill('[data-addenda-form] [data-input="text"]', "Mid-sprint addendum note.")
        page.click('[data-addenda-form] .button')
        page.wait_for_selector('[data-addenda] [data-addendum]', timeout=WAIT)
        addenda = page.query_selector_all('[data-addenda] [data-addendum]')
        assert len(addenda) == 1, len(addenda)
        assert "Mid-sprint addendum note." in page.inner_text('[data-addenda] [data-addendum]')
        ok("(s5) post-freeze: editors gone, frozen chip, no freeze button; addendum appended")

        # === Backlog & Ideas screen ==========================================
        nav(page, "#/backlog")
        page.wait_for_selector('[data-screen="backlog"] [data-create="item"]', timeout=WAIT)

        # (b1) item creates land NULL-sprint and list by server priority order.
        page.fill('[data-create="item"] [data-input="title"]', "Item Low")   # P3 default
        page.click('[data-create="item"] button[type="submit"]')
        page.wait_for_function(
            "() => document.querySelectorAll('[data-backlog-items] [data-item-id]').length === 1",
            timeout=WAIT,
        )
        page.fill('[data-create="item"] [data-input="title"]', "Item High")
        page.select_option('[data-create="item"] [data-input="priority"]', "P1")
        page.click('[data-create="item"] button[type="submit"]')
        page.wait_for_function(
            "() => document.querySelectorAll('[data-backlog-items] [data-item-id]').length === 2",
            timeout=WAIT,
        )
        rows = page.query_selector_all('[data-backlog-items] [data-item-id]')
        first_title = rows[0].query_selector('.entity-row-title').inner_text()
        second_title = rows[1].query_selector('.entity-row-title').inner_text()
        assert first_title == "Item High", first_title
        assert rows[0].query_selector('.chip--p1') is not None
        assert second_title == "Item Low", second_title
        assert rows[1].query_selector('.chip--p3') is not None
        ok("(b1) two item creates list by server priority: P1 'Item High' above P3 'Item Low'")

        # (b2) idea create appears in the ideas list.
        page.fill('[data-create="idea"] [data-input="title"]', "Search relevance idea")
        page.click('[data-create="idea"] button[type="submit"]')
        page.wait_for_selector('[data-ideas] [data-idea-id]', timeout=WAIT)
        idea_titles = page.eval_on_selector_all(
            '[data-ideas] [data-idea-id] .entity-row-title', "els => els.map(e => e.textContent)"
        )
        assert "Search relevance idea" in idea_titles, idea_titles
        ok("(b2) idea create appears: 'Search relevance idea' in the ideas list")

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
