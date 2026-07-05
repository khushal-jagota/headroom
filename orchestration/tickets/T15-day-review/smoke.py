#!/usr/bin/env python3
"""T15 Day + Review self-smoke. Boots in-process test-mode servers (uvicorn on a
daemon thread, temp DB, demo seed, frozen fake clock) and drives real headless
chromium (Playwright) through the two screens end to end: the Day screen (brief,
plan tree, ticket list, review entry, chat) and the Review walk (oldest-first,
grant-gated accept, edit-accept, skip, approve, accept-status). A second server
with the offline gateway proves the chat offline notice.

Straight-line, assert-based, numbered `ok NN` prints; any failed assertion raises
-> traceback + non-zero exit; the try/finally always stops every server, closes
the browser, and removes the temp dirs. Exits 0 with a final SMOKE PASS line on
success. Not under tests/ — never touched by pytest. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T15-day-review/smoke.py
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
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
PORT = 8795

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


def start_server(tmp: Path, today: str, gateway: str | None = None) -> dict:
    """Boot one seeded, normalized, in-process server on a daemon thread. The seed
    stamps real wall time while the clock is frozen at fake noon (demo.py:29-30 vs
    clock.py:27-41), so ticket timestamps are normalized to noon-600 — that keeps
    t5's review entry (updated_at fallback) the oldest approval (views.py:297-309)."""
    port = free_or(PORT)
    base = f"http://127.0.0.1:{port}"
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_FAKE_NOW": today + "T12:00:00",
        "PLAN_DB_PATH": str(tmp / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(tmp / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
        "PLAN_WS_POLL_MS": "50",  # tighten batch cadence; ui_debounce_ms stays 250
    }
    if gateway is not None:
        env["PLAN_GATEWAY_ADAPTER"] = gateway
    config = load_config(path=None, env=env)

    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
        create_schema(bootstrap)
    seed = connect(config.db_path, config.db_busy_timeout_ms)
    try:
        seed_demo(seed)
        anchor = int(datetime.fromisoformat(today + "T12:00:00").astimezone().timestamp()) - 600
        seed.execute("UPDATE tickets SET created_at = ?, updated_at = ?", (anchor, anchor))
        seed.commit()
    finally:
        seed.close()

    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory():
        return connect(config.db_path, config.db_busy_timeout_ms)

    app = create_app(config, clock, adapters, conn_factory)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_ready(base)
    return {"server": server, "thread": thread, "base": base}


def attr_list(page, selector: str, attr: str) -> list:
    return page.eval_on_selector_all(
        selector, f"els => els.map(e => e.getAttribute('{attr}'))"
    )


def ticket_ids(page) -> list:
    return attr_list(page, "[data-ticket-id]", "data-ticket-id")


def wait_row_count(page, count: int) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('[data-ticket-id]').length === n",
        arg=count,
        timeout=5000,
    )


def wait_enabled(page, selector: str) -> None:
    page.wait_for_function(
        "sel => { var b = document.querySelector(sel); return !!b && !b.disabled; }",
        arg=selector,
        timeout=5000,
    )


def main() -> int:
    os.chdir(REPO)  # create_app mounts StaticFiles(directory="assets") cwd-relative
    today = datetime.now().astimezone().date().isoformat()

    tmps: list[Path] = []
    servers: list[dict] = []
    playwright = None
    browser = None
    client: httpx.Client | None = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="t15-smoke-"))
        tmps.append(tmp)
        main_srv = start_server(tmp, today)
        servers.append(main_srv)
        base = main_srv["base"]
        client = httpx.Client(base_url=base, timeout=10.0)

        # Resolve demo ids (seed order; guarded by title, demo.py:56-113/247).
        day = client.get("/api/day/today").json()
        tks = day["tickets"]
        assert len(tks) == 3, tks
        t4, t8, t5 = tks[0]["id"], tks[1]["id"], tks[2]["id"]
        assert tks[0]["title"] == "Implement the demo feature slice.", tks[0]["title"]
        assert tks[1]["title"] == "Fix the flaky login test.", tks[1]["title"]
        assert tks[2]["title"] == "Review the analytics dashboard numbers.", tks[2]["title"]
        ns = client.get("/api/tickets", params={"state": "needs_success"}).json()["tickets"]
        assert len(ns) == 1, ns
        t1 = ns[0]["id"]
        todo = client.get("/api/items", params={"status": "todo"}).json()["items"]
        assert len(todo) == 1, todo
        item = todo[0]["id"]
        ok(f"server up; demo ids resolved (t1={t1}, t4={t4}, t8={t8}, t5={t5}, item={item})")

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch()
        page = browser.new_page()

        # (a) Day renders.
        page.goto(base + "/")
        page.wait_for_selector('.screen[data-screen="day"] [data-node="root"]', timeout=15000)
        page.wait_for_selector("[data-chat-panel] [data-chat-input]")
        assert "Demo day: ship the feature slice" in page.inner_text('.screen[data-screen="day"]')
        focus = page.inner_text('[data-node="root"] .plan-node-focus')
        assert focus == "Ship the demo feature slice.", focus
        assert page.eval_on_selector_all(".plan-node--child", "els => els.length") == 2
        assert attr_list(page, ".plan-node--child", "data-node") == ["0", "1"]
        assert attr_list(page, ".plan-node--child", "data-status") == ["accepted", "proposed"]
        assert ticket_ids(page) == [t4, t8, t5], ticket_ids(page)
        assert page.get_attribute("[data-pending-count]", "data-pending-count") == "1"
        assert page.inner_text("[data-pending-count]") == "1"
        assert page.query_selector("[data-chat-panel] [data-chat-send]") is not None
        ok("(a) day: brief, plan focus, 2 plan children, rows [t4,t8,t5], pending 1, chat present")

        # (b) Remove t8, then Accept-all re-adds it at the END of the list.
        page.click(f'[data-ticket-id="{t8}"] [data-remove]')
        wait_row_count(page, 2)
        assert ticket_ids(page) == [t4, t5], ticket_ids(page)
        page.click("[data-accept-all]")
        wait_row_count(page, 3)
        assert ticket_ids(page) == [t4, t5, t8], ticket_ids(page)
        statuses = attr_list(page, "[data-node]", "data-status")
        assert statuses == ["accepted", "accepted", "accepted"], statuses
        day_b = client.get("/api/day/today").json()
        assert day_b["plan"]["root"]["status"] == "accepted"
        assert all(ch["status"] == "accepted" for ch in day_b["plan"]["children"])
        assert [t["id"] for t in day_b["tickets"]] == [t4, t5, t8]
        ok("(b) remove + accept-all: list [t4,t5,t8], every plan node accepted (DOM + API)")

        # (c) Chat echo + flush survival.
        flushes_before = page.evaluate("window.__plannerDebug.flushes")
        page.fill("[data-chat-input]", "hello planner")
        page.click("[data-chat-send]")
        page.wait_for_selector('[data-chat-msg="you"]')
        page.wait_for_selector('[data-chat-msg="planner"]:has-text("echo: hello planner")')
        page.wait_for_function(
            "f => window.__plannerDebug.flushes > f", arg=flushes_before, timeout=5000
        )
        assert page.inner_text('[data-chat-msg="you"]') == "hello planner"
        assert page.query_selector(
            '[data-chat-msg="planner"]:has-text("echo: hello planner")'
        ) is not None
        assert client.get("/api/day/today").json()["chat_session_key"] == "fake-sess-1"
        ok("(c) chat: you + planner echo survive the session-created flush; session key persisted")

        # (d) Review setup via API (agent header parks each proposal).
        agent = {"X-Plan-Actor": "agent"}
        r = client.post(
            f"/api/tickets/{t1}/propose/success",
            json={"body": "Agent-drafted success criteria."},
            headers=agent,
        )
        assert r.status_code == 200, r.text
        sn1 = client.post("/api/test/set-now", json={"now": today + "T12:00:30"})
        assert sn1.status_code == 200, sn1.text
        r = client.post("/api/tickets", json={"title": "Fresh smoke ticket"})
        assert r.status_code == 200, r.text
        fresh = r.json()["id"]
        r = client.post(
            f"/api/tickets/{fresh}/propose/success",
            json={"body": "Fresh proposal body."},
            headers=agent,
        )
        assert r.status_code == 200, r.text
        sn2 = client.post("/api/test/set-now", json={"now": today + "T12:01:00"})
        assert sn2.status_code == 200, sn2.text
        r = client.post(f"/api/items/{item}/propose-status", json={"to": "done"}, headers=agent)
        assert r.status_code == 200, r.text
        approvals = client.get("/api/queues").json()["approvals"]
        order = [(a["entity_id"], a["kind"]) for a in approvals]
        assert order == [
            (t5, "review"), (t1, "success"), (fresh, "success"), (item, "status")
        ], order
        ok("(d) queue oldest-first: [t5 review, t1 success, fresh success, item status]")

        # (e) Review walk. Card 1 = t5 (review): skip, reload, approve.
        page.evaluate("window.location.hash = '#/review'")
        page.wait_for_selector(f'[data-review-card][data-entity-id="{t5}"]', timeout=10000)
        assert page.get_attribute("[data-review-card]", "data-kind") == "review"
        assert page.query_selector("[data-review-card] [data-approve]") is not None
        assert page.query_selector("[data-review-card] [data-grant-ceiling]") is None
        assert page.query_selector("[data-review-card] [data-edit]") is None
        card_text = page.inner_text("[data-review-card]")
        assert "Demo result for Review the analytics dashboard numbers." in card_text
        assert "Check the conversion query joins before approving." in card_text
        page.click("[data-review-card] [data-skip]")
        page.wait_for_selector(f'[data-review-card][data-entity-id="{t1}"]')
        page.reload()
        page.wait_for_selector(f'[data-review-card][data-entity-id="{t5}"]', timeout=10000)
        page.click("[data-review-card] [data-approve]")
        page.wait_for_selector(f'[data-review-card][data-entity-id="{t1}"]')
        assert client.get(f"/api/tickets/{t5}").json()["state"] == "done"
        ok("(e1) t5: review card (no grant/edit), skip advances, reload restores, approve -> done")

        # Card 2 = t1 (success): accept gated on BOTH grant halves; 'none' pins ceiling.
        c2 = f'[data-review-card][data-entity-id="{t1}"]'
        assert page.get_attribute(c2, "data-kind") == "success"
        assert page.get_attribute(c2, "data-field") == "success"
        assert page.is_disabled(f"{c2} [data-accept]")
        page.select_option(f"{c2} [data-grant-ceiling]", "none")
        assert page.is_disabled(f"{c2} [data-accept]")
        page.check(f'{c2} [data-grant-atcap] input[value="stop"]')
        wait_enabled(page, f"{c2} [data-accept]")
        page.click(f"{c2} [data-accept]")
        page.wait_for_selector(f'[data-review-card][data-entity-id="{fresh}"]')
        d1 = client.get(f"/api/tickets/{t1}").json()
        assert d1["state"] == "needs_approach", d1["state"]
        assert d1["ceiling"] == "needs_approach", d1["ceiling"]
        assert d1["at_cap"] == "stop", d1["at_cap"]
        assert d1["fields"]["success"]["value"] == "Agent-drafted success criteria."
        ok("(e2) t1: accept gated on both halves; none pins ceiling needs_approach; at_cap stop")

        # Card 3 = fresh (success): edit-accept stores altered text verbatim.
        c3 = f'[data-review-card][data-entity-id="{fresh}"]'
        assert page.input_value(f"{c3} [data-edit]") == "Fresh proposal body."
        page.fill(f"{c3} [data-edit]", "Human-edited body. ")
        page.check(f'{c3} [data-grant-atcap] input[value="propose"]')
        assert page.is_disabled(f"{c3} [data-accept]")
        page.select_option(f"{c3} [data-grant-ceiling]", "needs_plan")
        wait_enabled(page, f"{c3} [data-accept]")
        page.click(f"{c3} [data-accept]")
        page.wait_for_selector(f'[data-review-card][data-entity-id="{item}"]')
        d2 = client.get(f"/api/tickets/{fresh}").json()
        assert d2["fields"]["success"]["value"] == "Human-edited body. ", repr(
            d2["fields"]["success"]["value"]
        )
        assert d2["ceiling"] == "needs_plan", d2["ceiling"]
        assert d2["at_cap"] == "propose", d2["at_cap"]
        assert d2["state"] == "needs_approach", d2["state"]
        ok("(e3) fresh: prefill + edit-accept stores altered text verbatim; ceiling needs_plan")

        # Card 4 = item (status): accept without grant; queue empties.
        c4 = f'[data-review-card][data-entity-id="{item}"]'
        assert page.get_attribute(c4, "data-kind") == "status"
        assert page.query_selector(f"{c4} [data-grant-ceiling]") is None
        assert page.query_selector(f"{c4} [data-edit]") is None
        page.click(f"{c4} [data-accept-status]")
        page.wait_for_selector("[data-review-empty]")
        d3 = client.get(f"/api/items/{item}").json()
        assert d3["status"] == "done", d3["status"]
        assert d3["status_proposal"] is None
        page.wait_for_function(
            "() => { var b = document.querySelector('.nav-badge');"
            " return !!b && b.classList.contains('hidden'); }",
            timeout=5000,
        )
        ok("(e4) item: accept-status -> done; queue empty (data-review-empty); nav badge hidden")

        # (f) Offline gateway: chat shows the notice, no input source.
        tmp2 = Path(tempfile.mkdtemp(prefix="t15-offline-"))
        tmps.append(tmp2)
        off = start_server(tmp2, today, gateway="offline")
        servers.append(off)
        page2 = browser.new_page()
        page2.goto(off["base"] + "/")
        page2.wait_for_selector('.screen[data-screen="day"] [data-chat-offline]', timeout=15000)
        assert "Demo day: ship the feature slice" in page2.inner_text('.screen[data-screen="day"]')
        assert page2.eval_on_selector_all("[data-ticket-id]", "els => els.length") == 3
        assert page2.query_selector("[data-chat-send]") is None
        ok("(f) offline instance: chat-offline notice, no Send, brief + 3 rows intact")

        print(f"SMOKE PASS ({_CHECK} checks)")
        return 0
    finally:
        if client is not None:
            client.close()
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        for srv in servers:
            srv["server"].should_exit = True
            srv["thread"].join(timeout=10)
        for path in tmps:
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
