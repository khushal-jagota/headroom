"""Prove runtime → change stream → mounted shared conversation marks.

The material risk is that commit invalidation precedes settled runtime state,
or a consumer substitutes approval/child facts for its own conversation.
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
from tests.e2e.harness import REPO_ROOT, WAIT_MS, ApiHelper, JsonObject, ServerHandle

from planner.core.db import connect
from planner.worker_types.configuration import load_worker_runtime_definitions

EVIDENCE = REPO_ROOT / "data/verification/conversation-signals/final-40bfb962"


@pytest.fixture
def signal_server(request: pytest.FixtureRequest) -> Iterator[ServerHandle]:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"{request.node.callspec.params["layout"]}-", dir=EVIDENCE))
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
        # Preserve both process-owned runtime and durable database on failure.
        # The supervisor inspects the exact failed state before any repair.
        if (root / "complete.json").exists():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            print("PRESERVED SIGNAL FAILURE RUNTIME " + json.dumps(metadata))


def _post(server: ServerHandle, path: str, body: dict[str, object] | None = None) -> JsonObject:
    response = httpx.post(server.base + path, json=body or {}, timeout=10)
    assert response.is_success, response.text
    result: JsonObject = response.json()
    return result


def _marks(root: Locator, working: bool, message: bool) -> None:
    for kind, present in (("working", working), ("message", message)):
        expect(root.locator(f'[data-conversation-signal="{kind}"]')).to_have_count(
            int(present), timeout=WAIT_MS,
        )



def _capture(
    page: Page, root: Locator, conversation: str, surface: str, state: str,
    inventory: list[JsonObject], evidence: Path,
) -> None:
    filename = f"{conversation}-{surface}-{state}.png"
    if surface == "page-mark-area":
        bounds = root.bounding_box()
        assert bounds is not None
        page.screenshot(
            path=str(evidence / filename), animations="disabled",
            clip={**bounds, "height": min(bounds["height"], 220)},
        )
    else:
        root.screenshot(path=str(evidence / filename), animations="disabled")
    inventory.append({
        "role": conversation.removeprefix("proof-"), "surface": surface,
        "state": state, "file": filename,
    })


@pytest.mark.parametrize("layout", ["desktop", "compact"])
def test_exact_conversation_signals_cross_runtime_stream_and_surfaces(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    layout: str,
    request: pytest.FixtureRequest,
) -> None:
    server = signal_server
    evidence = server.db_path.parent / "evidence"
    evidence.mkdir()
    project = api.direct_post(server, "/api/projects", {"name": "Signal proof", "priority": "P1"})
    item = api.direct_post(server, "/api/items", {
        "title": "Signal orchestrator", "priority": "P1", "project_id": project["id"],
    })
    ticket = api.direct_post(server, "/api/tickets", {
        "title": "Signal Worker", "worker_type": "coding", "kickoff_note": "Agreed brief.",
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
    def capture_final() -> None:
        page.screenshot(path=str(evidence / "final-visible-state.png"), full_page=True)
        (evidence / "final-dom.html").write_text(page.content())
    request.addfinalizer(capture_final)
    page.set_viewport_size({"width": 1440 if layout == "desktop" else 560, "height": 1000})
    worker = page.locator(f'.board-workspace-left [data-ticket-id="{ticket["id"]}"]')
    supervisor = page.locator(f'[data-sprint-item="{item["id"]}"] .item-head')
    chief = page.locator("[data-chief-destination]")
    rows = {"proof-worker": worker, "proof-supervisor": supervisor, "proof-chief": chief}
    inventory: list[JsonObject] = []
    for conversation, row in rows.items():
        expect(row).to_be_visible(timeout=WAIT_MS)
        # Approval-only and completed-turn-only facts produce no mark.
        _marks(row, False, False)
        _capture(page, row, conversation, "row", "quiet", inventory, evidence)
    page.evaluate("""() => {
        window.signalProofEvents = [];
        window.signalProofStream = new EventSource('/api/changes');
        window.signalProofStream.onmessage = event => window.signalProofEvents.push(event.data);
    }""")
    page.wait_for_function("() => window.signalProofStream.readyState === 1", timeout=WAIT_MS)
    observations: list[JsonObject] = []
    for conversation, row in rows.items():
        # A second actual turn starts on an already durable conversation. A wake
        # caused only by conversation creation cannot satisfy this assertion.
        before = page.evaluate("window.signalProofEvents.length")
        _post(server, f"/api/test/signals/{conversation}/start")
        _marks(row, True, False)
        _capture(page, row, conversation, "row", "working-only", inventory, evidence)
        page.wait_for_function("before => window.signalProofEvents.length > before", arg=before,
                               timeout=WAIT_MS)
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        _marks(row, True, True)
        _capture(page, row, conversation, "row", "working-message", inventory, evidence)
        if conversation == "proof-worker":
            # A child's activity and explicit message never become the parent's marks.
            _marks(supervisor, False, False)
            _marks(chief, False, False)
        page.screenshot(path=str(evidence / f"{conversation}-working-message.png"), full_page=True)
        # Completing work preserves the explicit unread message.
        before = page.evaluate("window.signalProofEvents.length")
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(row, False, True)
        _capture(page, row, conversation, "row", "message-only", inventory, evidence)
        page.wait_for_function("before => window.signalProofEvents.length > before", arg=before,
                               timeout=WAIT_MS)
        _post(server, f"/api/test/signals/{conversation}/start")
        _marks(row, True, True)
        view = api.get(server, f"/api/conversation/conversations/{conversation}")
        assert view["is_running"] is True
        board = api.get(server, "/api/board")
        roster = api.get(server, "/api/workers")
        board_worker = next(card for column in board["columns"] for card in column["cards"]
                            if card["id"] == ticket["id"])
        board_supervisor = next(entry for entry in board["sprint_items"]
                                if entry["id"] == item["id"])
        assert board_worker["has_pending_proposal"] is True
        projections = {
            "proof-worker": board_worker,
            "proof-supervisor": board_supervisor,
            "proof-chief": roster["chief_of_staff"],
        }
        for projected_conversation, projection in projections.items():
            assert projection["conversation_signal"] == {
                "working": projected_conversation == conversation,
                "message_for_owner": projected_conversation == conversation,
            }
        observations.append({
            "conversation": conversation, "simultaneous": view,
            "board": board, "roster": roster,
        })
        # Read acknowledgement removes the dot and cannot stop ongoing work.
        _post(server, f"/api/conversation/conversations/{conversation}/owner-read", {
            "through_sequence": view["latest_sequence"],
        })
        _marks(row, True, False)
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(row, False, False)
        # A popup reads the same exact conversation without marking it read.
        _post(server, f"/api/test/signals/{conversation}/start")
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        _marks(row, True, True)
        row.hover()
        popup = page.locator('[data-rail-hover-panel][aria-hidden="false"]')
        expect(popup).to_be_visible(timeout=WAIT_MS)
        popup_signal = popup.locator(f'[data-conversation-id="{conversation}"]')
        _marks(popup_signal, True, True)
        _capture(page, popup, conversation, "popup", "working-message", inventory, evidence)
        # Keep this exact popup open across canonical changes. Hovering again could
        # replace a stale captured object and conceal the consumer freshness bug.
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(popup_signal, False, True)
        _capture(page, popup, conversation, "popup", "message-only", inventory, evidence)
        popup_view = api.get(server, f"/api/conversation/conversations/{conversation}")
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        later_view = api.get(server, f"/api/conversation/conversations/{conversation}")
        assert later_view["latest_sequence"] > popup_view["latest_sequence"]
        _post(server, f"/api/conversation/conversations/{conversation}/owner-read", {
            "through_sequence": popup_view["latest_sequence"],
        })
        _marks(popup_signal, False, True)
        observations.append({"conversation": conversation, "earlier_read_preserves_later": True,
                             "earlier": popup_view["latest_sequence"],
                             "later": later_view["latest_sequence"]})
        _post(server, f"/api/conversation/conversations/{conversation}/owner-read", {
            "through_sequence": later_view["latest_sequence"],
        })
        _marks(popup_signal, False, False)
        _capture(page, popup, conversation, "popup", "quiet", inventory, evidence)
        _post(server, f"/api/test/signals/{conversation}/start")
        _marks(popup_signal, True, False)
        _capture(page, popup, conversation, "popup", "working-only", inventory, evidence)
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        _marks(popup_signal, True, True)
        page.screenshot(path=str(evidence / f"{conversation}-popup.png"), full_page=True)
        # Hold the real acknowledgement briefly to observe simultaneous page marks;
        # then forward that exact browser request to the real owner-read endpoint.
        pending_reads: list[Route] = []

        def defer_read(route: Route, *, reads: list[Route] = pending_reads) -> None:
            reads.append(route)

        read_pattern = f"**/api/conversation/conversations/{conversation}/owner-read"
        page.route(read_pattern, defer_read)
        row.click()
        pane_signal = page.locator(
            f'.board-workspace-right [data-conversation-id="{conversation}"]',
        )
        _marks(pane_signal, True, True)
        page.mouse.move(1400 if layout == "desktop" else 540, 350)
        expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
        pane = page.locator(".board-workspace-right")
        _capture(page, pane, conversation, "page-mark-area", "working-message", inventory, evidence)
        page.screenshot(path=str(evidence / f"{conversation}-page.png"), full_page=True)
        # The read request is still held: ending work must preserve the message dot
        # on the mounted page, and starting real work restores only the spinner.
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(pane_signal, False, True)
        _capture(page, pane, conversation, "page-mark-area", "message-only", inventory, evidence)
        _post(server, f"/api/test/signals/{conversation}/start")
        _marks(pane_signal, True, True)
        if conversation == "proof-worker":
            expect(page.locator("[data-approval-block] h2")).to_contain_text("to approve")
            delivered = _post(server, f"/api/tickets/{ticket['id']}/conversation/send", {
                "conversation_id": conversation, "sender_label": "Owner", "mode": "steer",
                "content": [{"piece": "text", "text": "An ordinary owner reply."}],
            })
            assert delivered == {"conversation_id": conversation, "fate": "injected"}
            observations.append({"owner_reply": delivered})
            _marks(pane_signal, True, True)
        rest = page.locator(".board-workspace-right [data-conversation-rest-bar]")
        if rest.is_visible():
            with page.expect_request(read_pattern, timeout=WAIT_MS):
                rest.click()
            expect(page.locator('[data-conversation-state="peeked"]')).to_be_visible()
        assert pending_reads, "An eligible open conversation must acknowledge its message"
        for read_route in list(pending_reads):
            observations.append({
                "conversation": conversation, "browser_read": read_route.request.post_data,
            })
            read_route.continue_()
        page.unroute(read_pattern, defer_read)
        _marks(pane_signal, True, False)
        _capture(page, pane, conversation, "page-mark-area", "working-only", inventory, evidence)
        _marks(row, True, False)
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(pane_signal, False, False)
        _capture(page, pane, conversation, "page-mark-area", "quiet", inventory, evidence)
        _marks(row, False, False)
        if conversation == "proof-worker":
            lifecycle = page.locator("[data-ticket-lifecycle]")
            expect(lifecycle.locator("[data-ticket-retire-action]")).to_have_count(0)
            expect(lifecycle.get_by_role("button", name="Retire", exact=True)).to_have_count(0)
            backlog = lifecycle.locator("[data-ticket-backlog-action]")
            expect(backlog).to_have_text("Backlog ticket")
            backlog.click()
            expect(backlog).to_have_text("Resume ticket")
            backlog.click()
            expect(backlog).to_have_text("Backlog ticket")
        # Compact navigation intentionally hides the rail while a pane is selected.
        page.evaluate("location.hash = '#/workspace?view=items'")
        expect(worker).to_be_visible(timeout=WAIT_MS)
    reason = "Historical cancellation retained during signal proof."
    with connect(str(server.db_path)) as conn:
        row_record = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket["id"],)).fetchone()
        assert row_record is not None
        original = dict(row_record)
        values = json.loads(original["field_values"])
        values["retirement"] = json.dumps({"reason": reason, "original_record": original,
            "original_stage": original["stage"],
            "original_pending_proposal_json": original["pending_proposal"]})
        conn.execute("UPDATE tickets SET stage='cancelled',pending_proposal=NULL,"
                     "field_values=? WHERE id=?",
                     (json.dumps(values), ticket["id"]))
        conn.commit()
    page.evaluate("hash => location.hash = hash",
                  f"#/workspace/item/{item['id']}/{ticket['id']}")
    expect(page.locator("[data-ticket-cancelled]")).to_have_text("Cancelled")
    expect(page.locator("[data-retirement-reason]")).to_have_text(reason)
    expect(page.locator("[data-ticket-retire-action]")).to_have_count(0)
    expect(page.locator("[data-ticket-backlog-action]")).to_have_count(0)
    page.screenshot(path=str(evidence / "cancelled-history.png"), full_page=True)
    page.evaluate("hash => location.hash = hash", f"#/workspace/item/{item['id']}")
    children = page.locator(f'[data-orchestrator-children="sprint_item:{item["id"]}"]')
    children.get_by_label("Worker state", exact=True).select_option(label="Cancelled")
    cancelled_group = children.locator('[data-workspace-group="cancelled"]')
    expect(cancelled_group).to_be_attached(timeout=WAIT_MS)
    # Cancelled history is deliberately collapsed; this is the requested user action.
    if not cancelled_group.evaluate("element => element.open"):
        cancelled_group.locator(":scope > summary").click()
    cancelled_row = cancelled_group.locator(f'[data-sprint-ticket-id="{ticket["id"]}"]')
    expect(cancelled_row).to_be_visible(timeout=WAIT_MS)
    _marks(cancelled_row, False, False)
    page.screenshot(path=str(evidence / "cancelled-child-access.png"), full_page=True)
    cancelled_row.click()
    expect(page.locator("[data-retirement-reason]")).to_have_text(reason)
    events = page.evaluate("window.signalProofEvents")
    assert events and set(events) == {"change"}, events
    (evidence / "observations.json").write_text(json.dumps({
        "observations": observations, "change_events": events,
        "server": server.base, "db_path": str(server.db_path),
        "source": str(REPO_ROOT), "backend": "scripted adapter; real conversation core",
    }, indent=2))
    (evidence / "surface-inventory.json").write_text(json.dumps({
        "captures": inventory,
        "full_context": [
            f"{conversation}-{suffix}.png" for conversation in rows
            for suffix in ("working-message", "popup", "page")
        ],
        "coverage": {
            "rows": ["quiet", "working-only", "message-only", "working-message"],
            "popup": ["working-message", "message-only", "quiet", "working-only"],
            "page": ["working-message", "message-only", "working-only", "quiet"],
        },
        "popup_freshness": "One continuously open popup per role; no hover re-entry.",
        "limits": "Scripted backend wire; no production provider call or deployment proof.",
    }, indent=2))
    page.evaluate("window.signalProofStream.close()")
    (server.db_path.parent / "complete.json").write_text(json.dumps({"layout": layout}))


@pytest.mark.parametrize("layout", ["desktop", "compact"])
def test_unobscured_simultaneous_page_marks(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    layout: str,
) -> None:
    """Supplemental screenshots only; the transition matrix is a separate proof."""
    server = signal_server
    evidence = server.db_path.parent / "evidence"
    evidence.mkdir()
    project = api.direct_post(server, "/api/projects", {"name": "Signal proof", "priority": "P1"})
    item = api.direct_post(server, "/api/items", {
        "title": "Signal orchestrator", "project_id": project["id"],
    })
    ticket = api.direct_post(server, "/api/tickets", {
        "title": "Signal Worker", "worker_type": "coding", "kickoff_note": "Agreed brief.",
        "orchestrator": {"kind": "sprint_item", "id": item["id"]},
    })
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
        conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?",
                     ("proof-worker", ticket["id"]))
        conn.execute("INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
                     ("proof-worker", ticket["id"]))
        conn.execute("UPDATE agents SET conversation_id=? WHERE agent_key=?",
                     ("proof-supervisor", item["supervisor"]["agent_key"]))
        conn.execute("INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
                     "ON CONFLICT(agent_key) DO UPDATE "
                     "SET conversation_id=excluded.conversation_id",
                     ("proof-chief",))
        conn.commit()
    context = context_factory()
    page = open_page(context, server, "#/workspace", '[data-screen="workspace"]')
    page.set_viewport_size({"width": 1440 if layout == "desktop" else 560, "height": 1000})
    reads: list[Route] = []

    def hold_read(route: Route) -> None:
        reads.append(route)

    pattern = "**/api/conversation/conversations/*/owner-read"
    page.route(pattern, hold_read)
    routes = {
        "proof-worker": f"#/workspace/item/{item['id']}/{ticket['id']}",
        "proof-supervisor": f"#/workspace/item/{item['id']}",
        "proof-chief": "#/workspace/chief-of-staff",
    }
    inventory: list[JsonObject] = []
    for conversation, address in routes.items():
        _post(server, f"/api/test/signals/{conversation}/start")
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        page.evaluate("hash => location.hash = hash", address)
        pane = page.locator(".board-workspace-right")
        signal = pane.locator(f'[data-conversation-id="{conversation}"]')
        _marks(signal, True, True)
        page.mouse.move(1400 if layout == "desktop" else 540, 350)
        expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
        _capture(page, pane, conversation, "page-mark-area", "unobscured-both", inventory, evidence)
        page.screenshot(path=str(evidence / f"{conversation}-unobscured-page.png"), full_page=True)
        _post(server, f"/api/test/signals/{conversation}/finish")
    for read_route in list(reads):
        read_route.continue_()
    page.unroute(pattern, hold_read)
    (evidence / "supplemental-inventory.json").write_text(json.dumps({
        "captures": inventory, "layout": layout,
        "purpose": "Unobscured simultaneous page marks; supplemental to transition matrix.",
        "backend": "Controlled adapter over real conversation core; no provider execution.",
    }, indent=2))
    (server.db_path.parent / "complete.json").write_text(json.dumps({"layout": layout}))
