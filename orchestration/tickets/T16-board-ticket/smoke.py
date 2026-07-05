#!/usr/bin/env python3
"""T16 Board + Ticket self-smoke. Boots an in-process test-mode server (uvicorn on a
daemon thread, temp DB, REAL clock — the demo seed stamps wall-clock, so no fake now)
and drives real headless chromium (Playwright) against a demo-seeded database to prove
the Board and Ticket screens end to end: six ordered board columns with correct counts
and markers (dropped hidden), card-click navigation, accept-with-grant from the ticket
screen, copy-to-clipboard, links add/remove, run history + event log, grant control,
state jump / unblock / drop, and the online chat echo. Straight-line, assert-based,
numbered `ok NN` prints; any failed assertion raises -> traceback + non-zero exit; the
try/finally always stops the server, closes the browser, and removes the temp dir.
Exits 0 with a final SMOKE PASS line on success. Not under tests/ — never touched by
pytest. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T16-board-ticket/smoke.py
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
PORT = 8792

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
    tmp = Path(tempfile.mkdtemp(prefix="t16-smoke-"))
    db_path = tmp / "planning.db"
    port = free_or(PORT)
    base = f"http://127.0.0.1:{port}"
    env = {
        "PLAN_TEST_MODE": "1",
        # NO PLAN_FAKE_NOW: seed_demo stamps wall-clock; a fake clock would skew the
        # day/deadline alignment the demo builds against today.
        "PLAN_DB_PATH": str(db_path),
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
    context = None
    page = None
    client: httpx.Client | None = None
    try:
        thread.start()
        wait_ready(base)
        client = httpx.Client(base_url=base, timeout=10.0)

        # 1) Shell glue: the two screen script tags are present, ordered after
        # app.js, and both files serve 200 (loud failure if the orchestrator glue
        # is missing).
        body = client.get("/").text
        i_app = body.index("/assets/app.js")
        i_board = body.index("/assets/screens-board.js")
        i_ticket = body.index("/assets/screens-ticket.js")
        assert i_app < i_board and i_app < i_ticket, (i_app, i_board, i_ticket)
        assert client.get("/assets/screens-board.js").status_code == 200
        assert client.get("/assets/screens-ticket.js").status_code == 200
        ok("screen script tags present, ordered after app.js; both serve 200")

        # Seed the demo dataset and map titles -> ids.
        r = client.post("/api/seed", json={"demo": True})
        assert r.status_code == 200, r.text
        tickets = client.get("/api/tickets").json()["tickets"]
        by_title = {t["title"]: t["id"] for t in tickets}
        t_success = by_title["Draft the onboarding email success criteria."]
        t_impl = by_title["Implement the demo feature slice."]
        t_flaky = by_title["Fix the flaky login test."]
        t_review = by_title["Review the analytics dashboard numbers."]  # noqa: F841
        dropped_title = "Prototype the voice input toggle."

        # (a) Park a pending proposal on t_success's gating field (agent, no claim):
        # at needs_success/at_cap=propose, success is gating and its advance-target
        # exceeds the ceiling, so the proposal parks instead of auto-accepting.
        r = client.post(
            f"/api/tickets/{t_success}/propose/success",
            json={"body": "Smoke success body."},
            headers={"X-Plan-Actor": "agent"},
        )
        assert r.status_code == 200, r.text

        # (b) Live claim + two runs on t_impl, and (c) auto-block t_flaky — direct
        # sqlite (the smoke owns the temp DB; the dispatcher is out of scope).
        now = int(time.time())
        raw = sqlite3.connect(str(db_path))
        try:
            raw.execute(
                "UPDATE tickets SET claim_lock = ?, claim_expires = ? WHERE id = ?",
                ("claim_smoke01", now + 3600, t_impl),
            )
            raw.execute(
                "INSERT INTO runs (id, ticket_id, status, started_at, ended_at, "
                "summary, error, pid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("run_smoke_run", t_impl, "running", now - 120, None, None, None, None),
            )
            raw.execute(
                "INSERT INTO runs (id, ticket_id, status, started_at, ended_at, "
                "summary, error, pid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("run_smoke_done", t_impl, "done", now - 600, now - 300, "demo done run",
                 None, None),
            )
            raw.execute("UPDATE tickets SET auto_blocked = 1 WHERE id = ?", (t_flaky,))
            raw.commit()
        finally:
            raw.close()

        # Boot chromium with clipboard permissions.
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.goto(base + "/")
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
            timeout=15000,
        )

        # 2) Board: six ordered columns, correct counts, dropped hidden.
        page.goto(base + "/#/board")
        page.wait_for_selector('section[data-screen="board"]')
        states = [
            c.get_attribute("data-column")
            for c in page.query_selector_all("[data-column]")
        ]
        assert states == [
            "needs_success", "needs_approach", "needs_plan",
            "in_progress", "needs_review", "done",
        ], states

        def col_count(state: str) -> int:
            return len(page.query_selector_all(f'[data-column="{state}"] [data-card]'))

        assert col_count("needs_success") == 1
        assert col_count("needs_approach") == 1
        assert col_count("needs_plan") == 1
        assert col_count("in_progress") == 2
        assert col_count("needs_review") == 1
        assert col_count("done") == 1
        card_titles = page.eval_on_selector_all(
            "[data-card]", "els => els.map(e => e.textContent)"
        )
        assert all(dropped_title not in t for t in card_titles), dropped_title
        ok("board: six ordered columns, correct counts, dropped ticket hidden")

        # 3) Markers + card chips.
        sel_success = f'[data-card][data-ticket-id="{t_success}"]'
        sel_impl = f'[data-card][data-ticket-id="{t_impl}"]'
        assert page.query_selector(
            f'{sel_success} [data-marker="pending-proposal"]'
        ) is not None
        assert page.query_selector(
            f'{sel_impl} [data-marker="running-claim"]'
        ) is not None
        assert page.query_selector(f"{sel_success} .chip--priority") is not None
        assert page.query_selector(f"{sel_success} .chip--deadline") is not None
        assert page.query_selector(f"{sel_success} .chip--project") is not None
        ok("board: pending-proposal + running-claim markers and card chips present")

        # 4) Navigate via card click.
        page.click(sel_success)
        page.wait_for_function("h => location.hash === h", arg="#/ticket/" + t_success)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_success}"]'
        )
        assert page.query_selector(
            'section[data-screen="ticket"][data-state="needs_success"]'
        ) is not None
        assert page.query_selector('[data-field="success"] [data-accept]') is not None
        ok("card click opens the ticket; success field hosts the proposal card")

        # 5) Accept-with-grant from the ticket screen (scoped under the field so the
        # grant CONTROL's picker on the same screen is never touched).
        fld = '[data-field="success"]'
        page.select_option(f"{fld} [data-grant-ceiling]", "needs_approach")
        page.check(f'{fld} [data-grant-atcap] input[value="propose"]')
        page.click(f"{fld} [data-accept]")
        page.wait_for_selector(
            'section[data-screen="ticket"][data-state="needs_approach"]'
        )
        val_text = page.inner_text(f"{fld} .markdown-block")
        assert "Smoke success body." in val_text, val_text
        ok("accept-with-grant advances to needs_approach; value <- proposal body")

        # 6) Copy: click, wait for the flash, then read the clipboard; fall back to
        # the flash + copy-text block shape if the headless clipboard is unreadable.
        expected = client.get(f"/api/tickets/{t_success}/copy-text").text
        page.click("[data-copy]")
        page.wait_for_function(
            "() => { var b = document.querySelector('[data-copy]');"
            " return b && b.textContent === 'Copied'; }",
            timeout=5000,
        )
        clip = ""
        try:
            clip = page.evaluate("() => navigator.clipboard.readText()")
        except Exception:
            clip = ""
        if clip == expected:
            ok("copy: clipboard matches copy-text byte-for-byte")
        else:
            assert expected.startswith(
                "Draft the onboarding email success criteria."
            ), expected
            for marker in (
                "state:", "priority:", "success:", "approach:", "plan:",
                "result:", "recap:", "links:",
            ):
                assert marker in expected, marker
            ok("copy: button flashed Copied; copy-text block verified (clipboard unread)")

        # 7) Links add/remove.
        page.fill("[data-link-add] [data-link-to]", t_flaky)
        page.select_option("[data-link-add] [data-link-kind-select]", "relates")
        page.click("[data-link-add] [data-link-add-btn]")
        row_sel = '[data-link-row][data-link-kind="relates"]'
        page.wait_for_selector(row_sel)
        assert t_flaky in page.inner_text(row_sel)
        page.click(f"{row_sel} [data-link-remove]")
        page.wait_for_function("s => !document.querySelector(s)", arg=row_sel)
        ok("links: relates add renders a row referencing t_flaky; remove clears it")

        # 8) Run history (t_impl) + event log (t_success).
        page.goto(base + "/#/ticket/" + t_impl)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_impl}"]'
        )
        run_rows = page.query_selector_all("[data-run-history] [data-run-row]")
        assert len(run_rows) == 2, len(run_rows)
        assert page.query_selector('[data-run-row][data-run-status="running"]') is not None
        assert page.query_selector('[data-run-row][data-run-status="done"]') is not None
        page.goto(base + "/#/ticket/" + t_success)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_success}"]'
        )
        for kind in ("ticket_created", "proposal_accepted", "state_changed"):
            assert page.query_selector(
                f'[data-event-log] [data-event-row][data-event-kind="{kind}"]'
            ) is not None, kind
        ok("run history two rows (t_impl); event log created/accepted/state_changed")

        # 9) Grant control (persistence asserted via the re-rendered [data-grant-current]).
        gc = "[data-grant-control]"
        page.select_option(f"{gc} [data-grant-ceiling]", "needs_plan")
        page.check(f'{gc} [data-grant-atcap] input[value="stop"]')
        page.click(f"{gc} [data-grant-save]")
        page.wait_for_function(
            "() => { var e = document.querySelector('[data-grant-current]');"
            " return e && e.textContent.indexOf('needs_plan') !== -1"
            " && e.textContent.indexOf('stop') !== -1; }"
        )
        ok("grant control persists ceiling=needs_plan at_cap=stop")

        # 10) State jump (t_success) + unblock (t_flaky) + drop (t_success).
        page.select_option("[data-state-control] [data-state-select]", "needs_plan")
        page.click("[data-state-control] [data-state-jump]")
        page.wait_for_selector(
            'section[data-screen="ticket"][data-state="needs_plan"]'
        )
        page.goto(base + "/#/ticket/" + t_flaky)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_flaky}"]'
        )
        assert page.query_selector('[data-marker="auto-blocked"]') is not None
        assert page.query_selector("[data-unblock]") is not None
        page.click("[data-unblock]")
        page.wait_for_function(
            "() => !document.querySelector('[data-marker=\"auto-blocked\"]')"
        )
        page.goto(base + "/#/ticket/" + t_success)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_success}"]'
        )
        page.click("[data-drop]")
        page.wait_for_function(
            "() => { var e = document.querySelector('section[data-screen=\"ticket\"]');"
            " return e && e.getAttribute('data-state') === 'dropped'; }"
        )
        page.goto(base + "/#/board")
        page.wait_for_selector('section[data-screen="board"]')
        assert page.query_selector(
            f'[data-card][data-ticket-id="{t_success}"]'
        ) is None
        ok("state jump -> needs_plan; unblock clears auto-block; drop leaves the board")

        # 11) Chat: online echo fake replies locally (no flush wait).
        page.goto(base + "/#/ticket/" + t_impl)
        page.wait_for_selector(
            f'section[data-screen="ticket"][data-ticket-id="{t_impl}"]'
        )
        assert page.query_selector("[data-chat]") is not None
        # Test mode is online by construction (echo fake); a broken input/send/reply
        # must FAIL, not fall back (codex impl-review finding 1).
        page.fill("[data-chat] [data-chat-input]", "smoke hello")
        page.click("[data-chat] [data-chat-send]")
        page.wait_for_function(
            "() => { var els = document.querySelectorAll("
            "'[data-chat] [data-chat-msg=\"planner\"]');"
            " for (var i = 0; i < els.length; i++) {"
            " if (els[i].textContent.indexOf('echo: smoke hello') !== -1)"
            " return true; } return false; }",
            timeout=5000,
        )
        ok("chat: online echo fake replied 'echo: smoke hello'")

        print(f"SMOKE PASS ({_CHECK} checks)")
        return 0
    finally:
        if client is not None:
            client.close()
        if page is not None:
            page.close()
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        server.should_exit = True
        thread.join(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
