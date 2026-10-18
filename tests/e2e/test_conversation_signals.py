"""Prove runtime → change stream → mounted shared conversation marks.

The material risk is that commit invalidation precedes settled runtime state,
or a consumer substitutes approval/child facts for its own conversation.
The owner requires one visible indicator: message takes precedence over work;
a read must reveal the spinner while the same actual work continues.
Neither a component test nor an API-only test exercises that complete boundary.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import BrowserContext, Locator, Page, Route, expect
from tests.e2e.harness import (
    REPO_ROOT,
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
    capture_success,
)

from planner.core.db import connect
from planner.worker_types.configuration import load_worker_runtime_definitions

EVIDENCE = REPO_ROOT / "data/verification/conversation-signals/message-precedence-revision"


@pytest.fixture
def signal_server(request: pytest.FixtureRequest) -> Iterator[ServerHandle]:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    layout = getattr(request.node, "callspec", None)
    label = layout.params.get("layout", "proof") if layout else "proof"
    root = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=EVIDENCE))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    db = root / "conversation-signals-proof.db"
    env.update({
        "PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db), "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
        "PLAN_HERMES_HOME": str(root / "hermes-home"),
        "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
    })
    log_path = root / "server.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tests.e2e.conversation_signals_server"],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    server = ServerHandle(
        f"http://127.0.0.1:{port}", proc, db, log_path, port, root / "unused.sock",
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            assert proc.poll() is None, log_path.read_text()
            try:
                response = httpx.get(server.base + "/api/meta", timeout=1)
                if response.is_success:
                    assert response.json()["test_mode"]
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, log_path.read_text()
            time.sleep(0.1)
        with connect(str(db)) as conn:
            load_worker_runtime_definitions(conn)
        yield server
    finally:
        metadata = {"url": server.base, "pid": proc.pid, "runtime": str(root),
                    "db": str(db), "log": str(log_path)}
        (root / "runtime.json").write_text(json.dumps(metadata, indent=2))
        # Keep the exact database and logs, but never leak owned processes.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _post(server: ServerHandle, path: str, body: dict[str, object] | None = None) -> JsonObject:
    response = httpx.post(server.base + path, json=body or {}, timeout=10)
    assert response.is_success, response.text
    result: JsonObject = response.json()
    return result


def _marks(root: Locator, working: bool, message: bool) -> None:
    # Arguments are independent backend facts; presentation has one winner.
    for kind, present in (("working", working and not message), ("message", message)):
        expect(root.locator(f'[data-conversation-signal="{kind}"]')).to_have_count(
            int(present), timeout=WAIT_MS,
        )



@pytest.mark.parametrize("layout", ["desktop", "compact"])
def test_exact_conversation_signals_cross_runtime_stream_and_surfaces(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    layout: str,
) -> None:
    server = signal_server
    evidence = server.db_path.parent / "evidence"
    evidence.mkdir()
    project = api.direct_post(server, "/api/projects", {"name": "Signal proof", "priority": "P1"})
    item = api.direct_post(server, "/api/items", {
        "title": "Signal orchestrator", "priority": "P1", "project_id": project["id"],
    })
    ticket = api.direct_post(server, "/api/tickets", {
        "title": "Signal Worker", "worker_type": "coding", "brief": "Agreed brief.",
        "orchestrator": {"kind": "sprint_item", "id": item["id"]},
    })
    api.direct_post(server, f"/api/tickets/{ticket['id']}/accept/brief", {
        "next_ceiling": "needs_success_condition",
        "next_holder": {"kind": "owner", "id": "owner"},
    })
    proposed = httpx.post(
        server.base + f"/api/tickets/{ticket['id']}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
        json={"body": "Quiet approval remains visible.", "recap": "Browser signal proof."},
        timeout=10,
    )
    assert proposed.is_success, proposed.text
    identities = {
        "proof-worker": {"kind": "ticket", "id": ticket["id"], "label": "Signal Worker"},
        "proof-supervisor": {
            "kind": "sprint_item", "id": item["id"], "label": "Signal orchestrator",
        },
        "proof-chief": {"kind": "chief", "id": "chief", "label": "Chief of Staff"},
    }
    for conversation in identities:
        _post(server, f"/api/test/signals/{conversation}/start")
        _post(server, f"/api/test/signals/{conversation}/finish")
    with connect(str(server.db_path)) as conn:
        conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?", (
            "proof-worker", ticket["id"],
        ))
        conn.execute("INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)", (
            "proof-worker", ticket["id"],
        ))
        conn.execute("UPDATE agents SET conversation_id=? WHERE agent_key=?", (
            "proof-supervisor", item["supervisor"]["agent_key"],
        ))
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            ("proof-chief",),
        )
        conn.commit()
    context = context_factory()
    page = open_page(context, server, "#/workspace?view=items", '[data-screen="workspace"]')
    page.set_viewport_size({"width": 1440 if layout == "desktop" else 560, "height": 1000})
    worker = page.locator(f'.board-workspace-left [data-ticket-id="{ticket["id"]}"]')
    supervisor = page.locator(f'[data-sprint-item="{item["id"]}"] .item-head')
    chief = page.locator("[data-chief-destination]")
    rows = {"proof-worker": worker, "proof-supervisor": supervisor, "proof-chief": chief}
    for row in rows.values():
        expect(row).to_be_visible(timeout=WAIT_MS)
        _marks(row, False, False)
    page.evaluate("""() => {
        window.signalProofEvents = [];
        window.signalProofStream = new EventSource('/api/changes');
        window.signalProofStream.onmessage = event => window.signalProofEvents.push(event.data);
    }""")
    page.wait_for_function("() => window.signalProofStream.readyState === 1", timeout=WAIT_MS)
    observations: list[JsonObject] = []
    selected = rows if layout == "desktop" else {"proof-worker": worker}
    for conversation, row in selected.items():
        popup_signal: Locator | None = None
        if layout == "desktop":
            # Keep the same popup mounted before the actual second turn starts.
            row.hover()
            popup = page.locator('[data-rail-hover-panel][aria-hidden="false"]')
            expect(popup).to_be_visible(timeout=WAIT_MS)
            popup_signal = popup.locator(f'[data-conversation-id="{conversation}"]')
            _marks(popup_signal, False, False)
        before = page.evaluate("window.signalProofEvents.length")
        _post(server, f"/api/test/signals/{conversation}/start")
        if popup_signal is not None:
            _marks(row, True, False)
            _marks(popup_signal, True, False)
        page.wait_for_function("before => window.signalProofEvents.length > before", arg=before,
                               timeout=WAIT_MS)
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        _marks(row, True, True)
        if popup_signal is not None:
            _marks(popup_signal, True, True)
        if conversation == "proof-worker":
            _marks(supervisor, False, False)
            _marks(chief, False, False)
        if conversation == "proof-worker" and popup_signal is not None:
            # Worker popup resolves the current board card, not captured props.
            view = api.get(server, f"/api/conversation/conversations/{conversation}")
            _post(server, f"/api/conversation/conversations/{conversation}/owner-read", {
                "through_sequence": view["latest_sequence"],
            })
            _marks(popup_signal, True, False)
            _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
            _marks(popup_signal, True, True)
        pending_reads: list[Route] = []

        def defer_read(route: Route, *, reads: list[Route] = pending_reads) -> None:
            reads.append(route)

        read_pattern = f"**/api/conversation/conversations/{conversation}/owner-read"
        page.route(read_pattern, defer_read)
        row.click()
        pane = page.locator(".board-workspace-right")
        pane_signal = pane.locator(f'[data-conversation-id="{conversation}"]')
        _marks(pane_signal, True, True)
        page.mouse.move(1400 if layout == "desktop" else 540, 350)
        expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
        if conversation == "proof-worker" and layout == "desktop":
            expect(page.locator("[data-approval-block] h2")).to_contain_text("to approve")
            delivered = _post(server, f"/api/tickets/{ticket['id']}/conversation/send", {
                "conversation_id": conversation, "sender_label": "Owner", "mode": "steer",
                "content": [{"piece": "text", "text": "An ordinary owner reply."}],
            })
            assert delivered == {"conversation_id": conversation, "fate": "injected"}
            _marks(pane_signal, True, True)
        capture_success(page, evidence / f"{conversation}-{layout}-page-message.png")
        rest = pane.locator("[data-conversation-rest-bar]")
        if rest.is_visible():
            with page.expect_request(read_pattern, timeout=WAIT_MS):
                rest.click()
            expect(page.locator('[data-conversation-state="peeked"]')).to_be_visible()
        assert pending_reads, "An eligible open conversation must acknowledge its message"
        for read_route in list(pending_reads):
            observations.append({"conversation": conversation,
                                 "browser_read": read_route.request.post_data})
            read_route.continue_()
        page.unroute(read_pattern, defer_read)
        _marks(pane_signal, True, False)
        _marks(row, True, False)
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(pane_signal, False, False)
        _marks(row, False, False)
        page.evaluate("location.hash = '#/workspace?view=items'")
        expect(row).to_be_visible(timeout=WAIT_MS)
        _marks(row, False, False)
    events = page.evaluate("window.signalProofEvents")
    assert events and set(events) == {"change"}, events
    (evidence / "observations.json").write_text(json.dumps({
        "observations": observations, "change_events": events,
        "layout": layout, "server": server.base, "db_path": str(server.db_path),
        "backend": "scripted adapter; real conversation core; real fixed refresh window",
    }, indent=2))
    page.evaluate("window.signalProofStream.close()")
