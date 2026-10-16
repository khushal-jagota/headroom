"""Prove rail writes, invalidation and navigation against an isolated live server.

Settle/Reopen can hide outstanding work or replace the open Worker if the HTTP write,
board projection, SSE invalidation and route selection disagree. Frontend mocks and
backend tests cannot prove those boundaries together. Helper tests own grouping rules;
this small browser proof owns the connected behavior and keyboard/pointer access.
No backend prompts occur: deterministic durable messages provide unread evidence.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import REPO_ROOT, WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_approval_groups_message_dots import _conversation, _message
from tests.e2e.test_ticket_conversation_shared_behaviors import _append_rows

from planner.conversation.events import UserInputQuestion, UserInputRequestedEventPayload
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.worker_types.configuration import load_worker_runtime_definitions

EVIDENCE = Path("/home/vps/Deployments/Panels/current/data/files/tickets/t_9fh1vzxc/artifacts/rail")
LONG_TITLE = "Review the entire Worker title when the adjustable rail clips this long approval row"


@pytest.fixture
def rail_server(tmp_path: Path) -> Iterator[ServerHandle]:
    # Every PLAN_* input is explicit and isolated, including the agent home.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    db = tmp_path / "rail-proof.db"
    log_path = tmp_path / "server.log"
    env.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db),
            "PLAN_PORT": str(port),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes-home"),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        }
    )
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.e2e.rail_server"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    server = ServerHandle(
        f"http://127.0.0.1:{port}", process, db, log_path, port, tmp_path / "unused.sock"
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, log_path.read_text()
            try:
                response = httpx.get(server.base + "/api/meta", timeout=1)
                if response.is_success:
                    assert response.json()["test_mode"] is True
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, log_path.read_text()
            time.sleep(0.1)
        with connect(str(db)) as conn:
            load_worker_runtime_definitions(conn)
        yield server
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _seed(server: ServerHandle, api: ApiHelper) -> tuple[JsonObject, JsonObject, JsonObject]:
    project = api.direct_post(server, "/api/projects", {"name": "Rail proof", "priority": "P1"})
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Review the connected retirement rail",
            "priority": "P1",
            "project_id": project["id"],
        },
    )
    empty = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Empty orchestrator stays visible",
            "priority": "P2",
            "project_id": project["id"],
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": LONG_TITLE,
            "worker_type": "coding",
            "kickoff_note": "Review the brief.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    # Empty and Blocked children belong in the hover details, not attention groups.
    for title, blockers in (
        ("Quiet ordinary Worker", []),
        ("Blocked ordinary Worker", [ticket["id"]]),
    ):
        child = api.direct_post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "kickoff_note": "Agreed brief.",
                "orchestrator": {"kind": "sprint_item", "id": item["id"]},
                "blocked_by_ticket_ids": blockers,
            },
        )
        api.direct_post(
            server,
            f"/api/tickets/{child['id']}/accept/brief",
            {"next_ceiling": "none", "next_holder": {"kind": "owner", "id": "owner"}},
        )
    question = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Answer only Worker with no direct message",
            "worker_type": "coding",
            "kickoff_note": "Agreed brief.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    api.direct_post(
        server,
        f"/api/tickets/{question['id']}/accept/brief",
        {"next_ceiling": "none", "next_holder": {"kind": "owner", "id": "owner"}},
    )
    _conversation(server, "rail-question")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?", ("rail-question", question["id"])
        )
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("rail-question", question["id"]),
        )
    _append_rows(
        server,
        "rail-question",
        UserInputRequestedEventPayload(
            request_id="rail-question",
            questions=(
                UserInputQuestion(
                    question_id="scope",
                    header="Scope",
                    question="Which result do you want?",
                    options=(),
                    multi_select=False,
                    allow_other=True,
                ),
            ),
        ),
    )
    _conversation(server, "rail-unread")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?", ("rail-unread", ticket["id"])
        )
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("rail-unread", ticket["id"]),
        )
    _message(
        server, "rail-unread", Principal(PrincipalKind.ticket, ticket["id"]), "Rail direct message"
    )
    for conversation in ("rail-chief", "rail-supervisor"):
        _conversation(server, conversation)
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE agents SET conversation_id=? WHERE agent_key=?",
            ("rail-supervisor", item["supervisor"]["agent_key"]),
        )
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            ("rail-chief",),
        )
    return item, empty, ticket


def test_settle_reopen_keeps_outstanding_work_and_worker_address(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    item, empty, ticket = _seed(server, api)
    context = context_factory()
    page = open_page(context, server, "#/workspace?view=items", '[data-screen="workspace"]')
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1000})
    left = page.locator(".board-workspace-left")
    card = left.locator(f'[data-sprint-item="{item["id"]}"]')
    row = card.locator(f'[data-ticket-id="{ticket["id"]}"]')
    expect(left.locator("[data-chief-destination]")).to_be_visible(timeout=WAIT_MS)
    expect(left.locator("[data-sprint-item]").first).to_have_attribute(
        "data-sprint-item", item["id"]
    )
    expect(left.locator(".orchestrator-card").first).to_have_class(re.compile(r"\bchief\b"))
    expect(
        left.locator(".orchestrator-card.chief").get_by_role("button", name="Settle", exact=False)
    ).to_have_count(0)
    expect(card.locator(".orchestrator-eyebrow")).to_contain_text("Rail proof")
    expect(left.locator(f'[data-sprint-item="{empty["id"]}"]')).to_be_visible()
    expect(row.get_by_role("img", name="Unread reply")).to_be_visible(timeout=WAIT_MS)
    expect(card.locator(".item-head .stage-mark")).to_have_count(0)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
    question_row = card.get_by_role(
        "button", name="Answer only Worker with no direct message", exact=True
    )
    expect(question_row).to_be_visible(timeout=WAIT_MS)
    expect(question_row.get_by_role("img")).to_have_count(0)
    expect(card.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(LONG_TITLE)
    row.click()
    worker_hash = page.evaluate("location.hash")
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    with page.expect_response(
        lambda response: (
            response.url.endswith(f"/api/items/{item['id']}/settled")
            and response.request.method == "PATCH"
        )
    ) as write:
        card.get_by_role("button", name=f"Settle {item['title']}", exact=True).click()
    assert write.value.ok
    expect(card).to_have_count(0, timeout=WAIT_MS)
    assert write.value.json()["settled"] is True
    assert page.evaluate("location.hash") == worker_hash
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    # Settlement changes arrangement only. Existing global views retain attention.
    page.goto(server.base + "/#/review")
    expect(page.locator(f'[data-review-card][data-ticket-id="{ticket["id"]}"]')).to_be_visible(
        timeout=WAIT_MS
    )
    page.goto(server.base + "/#/backlog")
    global_row = page.locator(f'[data-backlog-tickets] [data-ticket-id="{ticket["id"]}"]')
    expect(global_row).to_be_visible(timeout=WAIT_MS)
    expect(global_row).to_contain_text(LONG_TITLE)
    global_row.click()
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    page.go_back()
    expect(global_row).to_be_visible(timeout=WAIT_MS)
    page.goto(server.base + "/" + worker_hash)
    page.reload()
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    assert page.evaluate("location.hash") == worker_hash
    left.get_by_role("button", name="Settled", exact=False).click()
    settled = left.locator(f'[data-settled-item="{item["id"]}"]')
    expect(settled).to_be_visible()
    settled.get_by_role("button", name="Reopen", exact=False).click()
    expect(card).to_be_visible(timeout=WAIT_MS)
    with sqlite3.connect(server.db_path) as conn:
        assert (
            conn.execute("SELECT settled FROM sprint_items WHERE id=?", (item["id"],)).fetchone()[0]
            == 0
        )
    expect(row.get_by_role("img", name="Unread reply")).to_be_visible()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / "rail-1440.png"), full_page=True)
    _message(server, "rail-chief", CHIEF_PRINCIPAL, "Chief own direct message")
    _message(
        server,
        "rail-supervisor",
        Principal(PrincipalKind.sprint_item, item["id"]),
        "Supervisor own direct message",
    )
    page.reload()
    expect(card.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_be_visible(timeout=WAIT_MS)
    expect(row.get_by_role("img", name="Unread reply")).to_be_visible()
    api.direct_post(
        server, "/api/conversation/conversations/rail-chief/owner-read", {"through_sequence": 1}
    )
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0, timeout=WAIT_MS)
    expect(card.locator(".item-head .stage-mark")).to_be_visible()
    expect(row.get_by_role("img", name="Unread reply")).to_be_visible()
    page.screenshot(path=str(EVIDENCE / "own-conversation-marks.png"), full_page=True)
    shutil.copyfile(server.log_path, EVIDENCE / "isolated-server.log")


def test_rail_resize_and_hover_are_accessible_without_losing_selection(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    item, _, ticket = _seed(server, api)
    page = open_page(
        context_factory(), server, "#/workspace?view=items", '[data-screen="workspace"]'
    )
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1000})
    left = page.locator(".board-workspace-left")
    card = left.locator(f'[data-sprint-item="{item["id"]}"]')
    row = card.locator(f'[data-ticket-id="{ticket["id"]}"]')
    grip = page.get_by_role("separator", name="Resize workspace panel")
    page.wait_for_function(
        """() => Math.abs(
        Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow')) -
        document.querySelector('.board-workspace-left').getBoundingClientRect().width
    ) < 2""",
        timeout=WAIT_MS,
    )
    grip.focus()
    expect(grip).to_be_focused()
    original = left.evaluate("el => el.getBoundingClientRect().width")
    grip.press("ArrowRight")
    grown = left.evaluate("el => el.getBoundingClientRect().width")
    assert grown > original
    page.reload()
    expect(grip).to_be_visible()
    assert abs(left.evaluate("el => el.getBoundingClientRect().width") - grown) < 2
    bounds = grip.bounding_box()
    assert bounds
    page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + 50)
    page.mouse.down()
    page.mouse.move(bounds["x"] + 65, bounds["y"] + 50, steps=4)
    page.mouse.up()
    assert left.evaluate("el => el.getBoundingClientRect().width") > grown
    grip.dblclick()
    assert abs(left.evaluate("el => el.getBoundingClientRect().width") - original) < 2
    row.focus()
    expect(row).to_be_focused()
    row.hover()
    pop = page.locator("[data-rail-hover-panel]")
    expect(pop).to_contain_text(LONG_TITLE, timeout=WAIT_MS)
    expect(pop).to_contain_text("Stages")
    expect(pop).to_contain_text("Brief")
    # Moving directly to another target changes the same visible popup in place.
    card.locator(".item-head").hover()
    expect(pop).to_contain_text("Quiet ordinary Worker", timeout=WAIT_MS)
    expect(pop).to_contain_text("Blocked ordinary Worker")
    expect(pop).not_to_contain_text("Stages")
    assert page.locator("[data-rail-hover-panel]").count() == 1
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / "rail-hover-1440.png"), full_page=True)
    page.set_viewport_size({"width": 820, "height": 1000})
    row.focus()
    expect(row).to_be_focused()
    expect(row).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(EVIDENCE / "rail-narrow-820.png"), full_page=True)


def _send_owner_message(server: ServerHandle, sender: Principal, message: str) -> None:
    """Use the normal authenticated message route, backed by the durable test adapter."""
    headers = {
        PrincipalKind.ticket: {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": sender.id},
        PrincipalKind.sprint_item: {
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": sender.id,
        },
        PrincipalKind.chief: {"X-Plan-Actor": "chief"},
    }[sender.kind]
    response = httpx.post(
        server.base + "/api/messages/send",
        headers=headers,
        json={
            "target": {"kind": "owner", "id": "owner"},
            "message": message,
        },
        timeout=10,
    )
    assert response.is_success, response.text
    assert response.json()["fate"] == "recorded"


def test_settled_messages_keep_exact_rows_and_item_counts_after_reads(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    """A folded settled conversation must stay findable across canonical read updates.

    This risk spans durable message/read state, real board projections, change-stream
    invalidation and rendered rows/routes. No mocked frontend query or backend-only
    assertion proves the connected owner experience. Header counts name items. Exact row
    dots retain unread facts. Terminal rows retain their local lifecycle group.
    """
    server = rail_server
    project = api.direct_post(server, "/api/projects", {"name": "Settled proof", "priority": "P1"})
    parents = [
        api.direct_post(
            server, "/api/items", {"title": title, "priority": "P1", "project_id": project["id"]}
        )
        for title in ("Settled with own message", "Quiet settled parent")
    ]
    children = [
        api.direct_post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "kickoff_note": "Historical brief.",
                "orchestrator": {"kind": "sprint_item", "id": parent["id"]},
            },
        )
        for title, parent in zip(
            ("Completed child with two messages", "Child of quiet parent"), parents, strict=True
        )
    ]
    for name in ("settled-child-one", "settled-child-two", "settled-supervisor", "settled-chief"):
        _conversation(server, name)
    with sqlite3.connect(server.db_path) as conn:
        for child, name in zip(children, ("settled-child-one", "settled-child-two"), strict=True):
            conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?", (name, child["id"]))
            conn.execute(
                "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
                (name, child["id"]),
            )
        # A historical terminal Worker is still an exact message destination. This is
        # isolated fixture state, never a claim that the owner's actual work completed.
        conn.execute(
            "UPDATE tickets SET stage='done',pending_proposal=NULL WHERE id=?", (children[0]["id"],)
        )
        conn.execute(
            "UPDATE agents SET conversation_id=? WHERE agent_key=?",
            ("settled-supervisor", parents[0]["supervisor"]["agent_key"]),
        )
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            ("settled-chief",),
        )
    for parent in parents:
        api.direct_patch(server, f"/api/items/{parent['id']}/settled", {"settled": True})
    for sender, text in (
        (Principal(PrincipalKind.ticket, children[0]["id"]), "First message in same conversation"),
        (Principal(PrincipalKind.ticket, children[0]["id"]), "Second message in same conversation"),
        (
            Principal(PrincipalKind.ticket, children[1]["id"]),
            "Child message without parent message",
        ),
        (Principal(PrincipalKind.sprint_item, parents[0]["id"]), "Own supervisor message"),
        (CHIEF_PRINCIPAL, "Own Chief message"),
    ):
        _send_owner_message(server, sender, text)

    page = open_page(context_factory(), server, "#/workspace", '[data-screen="workspace"]')
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1100})
    settlement_writes: list[str] = []
    page.on(
        "request",
        lambda request: (
            settlement_writes.append(request.url)
            if request.method == "PATCH" and request.url.endswith("/settled")
            else None
        ),
    )
    left = page.locator(".board-workspace-left")
    # Discovery stays nested in the approved rail; there is no global Messages block.
    expect(left.locator(":scope > [data-workspace-messages]")).to_have_count(0)
    settled_head = left.locator(".settled-head")
    expect(settled_head).to_have_attribute("aria-expanded", "false")
    expect(left.locator("[data-settled-count]")).to_have_text("(2)")
    expect(settled_head.get_by_role("img")).to_have_count(0)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_be_visible()
    settled_head.focus()
    page.keyboard.press("Enter")
    expect(settled_head).to_have_attribute("aria-expanded", "true")
    first_parent = left.locator(f'[data-settled-item="{parents[0]["id"]}"]')
    quiet_parent = left.locator(f'[data-settled-item="{parents[1]["id"]}"]')
    expect(first_parent.locator(".settled-title .stage-mark")).to_be_visible()
    expect(quiet_parent.locator(".settled-title .stage-mark")).to_have_count(0)
    rows = {
        "settled-child-one": first_parent.locator(
            '[data-message-conversation="settled-child-one"]'
        ),
        "settled-child-two": quiet_parent.locator(
            '[data-message-conversation="settled-child-two"]'
        ),
        "settled-supervisor": first_parent.locator(".settled-title"),
        "settled-chief": left.locator("[data-chief-destination]"),
    }
    for row in rows.values():
        expect(row).to_be_visible()
        expect(row.get_by_role("img", name="Unread reply")).to_have_count(1)
    expect(first_parent.locator("[data-workspace-messages] .bucket-label")).to_have_text("Messages")
    expect(quiet_parent.locator("[data-workspace-messages] .bucket-label")).to_have_text("Messages")
    expect(left.locator(".message-context")).to_have_count(0)
    expect(rows["settled-child-one"]).to_have_attribute(
        "href", f"#/workspace/item/{parents[0]['id']}/{children[0]['id']}"
    )
    rows["settled-child-two"].focus()
    expect(rows["settled-child-two"]).to_be_focused()
    rows["settled-child-one"].click()
    expect(
        page.locator(f'[data-screen="ticket"][data-ticket-id="{children[0]["id"]}"]')
    ).to_be_visible()
    rows["settled-supervisor"].click()
    pane = page.locator(f'[data-sprint-item-workspace="{parents[0]["id"]}"]')
    expect(pane).to_be_visible()
    # The local page no longer duplicates Messages. Terminal Workers keep their
    # lifecycle group while the nested settled Messages bucket keeps exact discovery.
    expect(pane.locator('[data-workspace-section="remaining"]')).to_have_count(0)
    expect(pane.locator('[data-workspace-group="awaiting_reply"]')).to_have_count(0)
    page_group = pane.locator('[data-workspace-group="done"]')
    expect(page_group).to_be_attached()
    expect(page_group).not_to_have_attribute("open", "")
    expect(page_group.locator("[data-group-count]")).to_have_text("1")
    expect(page_group.locator("[data-group-count]")).to_be_visible()
    page_group.locator(":scope > summary").focus()
    page.keyboard.press("Enter")
    expect(page_group).to_have_attribute("open", "")
    terminal_row = page_group.locator(f'[data-sprint-ticket-id="{children[0]["id"]}"]')
    expect(terminal_row).to_be_visible()
    expect(terminal_row.get_by_role("img", name="Unread reply")).to_have_count(1)
    page_group.locator(":scope > summary").click()
    artifacts = EVIDENCE.parent / "settled-messages"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / "settled-and-messages-1440.png"), full_page=True)

    # A read advances the durable position and the same SSE invalidation clears only
    # the exact dot/message row. Ordinary counts stay stable without a reload/refetch.
    api.direct_post(
        server,
        "/api/conversation/conversations/settled-supervisor/owner-read",
        {"through_sequence": 1},
    )
    expect(first_parent.locator(".settled-title .stage-mark")).to_have_count(0)
    expect(left.locator("[data-settled-count]")).to_have_text("(2)")
    expect(left.locator("[data-chief-destination] .stage-mark")).to_be_visible()
    api.direct_post(
        server,
        "/api/conversation/conversations/settled-child-one/owner-read",
        {"through_sequence": 2},
    )
    expect(rows["settled-child-one"]).to_have_count(0, timeout=WAIT_MS)
    expect(left.locator("[data-settled-count]")).to_have_text("(2)")
    # Reading clears the exact dot without moving or changing the terminal row count.
    done = pane.locator('[data-workspace-group="done"]')
    expect(done).to_be_attached(timeout=WAIT_MS)
    if not done.evaluate("el => el.open"):
        done.locator(":scope > summary").click()
    expect(done.locator(f'[data-sprint-ticket-id="{children[0]["id"]}"]')).to_be_visible()
    expect(done.locator("[data-group-count]")).to_have_text("1")
    expect(done.locator("[data-group-count]")).to_be_visible()
    expect(done.locator(".stage-mark")).to_have_count(0)
    api.direct_post(
        server,
        "/api/conversation/conversations/settled-child-two/owner-read",
        {"through_sequence": 1},
    )
    expect(rows["settled-child-two"]).to_have_count(0, timeout=WAIT_MS)
    expect(left.locator("[data-settled-count]")).to_have_text("(2)")
    api.direct_post(
        server, "/api/conversation/conversations/settled-chief/owner-read", {"through_sequence": 1}
    )
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0, timeout=WAIT_MS)
    settled_head.click()
    _send_owner_message(
        server,
        Principal(PrincipalKind.ticket, children[0]["id"]),
        "A later message in the same settled conversation",
    )
    expect(left.locator("[data-settled-count]")).to_have_text("(2)")
    expect(quiet_parent.locator(".settled-title .stage-mark")).to_have_count(0)
    expect(first_parent.locator(".settled-title .stage-mark")).to_have_count(0)
    settled_head.focus()
    page.keyboard.press("Enter")
    expect(rows["settled-child-one"]).to_be_visible()
    expect(rows["settled-child-one"].get_by_role("img", name="Unread reply")).to_have_count(1)
    expect(terminal_row.get_by_role("img", name="Unread reply")).to_have_count(1)
    expect(page_group.locator("[data-group-count]")).to_have_text("1")
    expect(page_group.locator("[data-group-count]")).to_be_visible()
    page.screenshot(path=str(artifacts / "later-message-without-reopen.png"), full_page=True)
    chief_child = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Unread child assigned explicitly to Chief",
            "worker_type": "coding",
            "kickoff_note": "Review this brief.",
            "orchestrator": {"kind": "chief", "id": "chief"},
        },
    )
    _conversation(server, "settled-chief-child")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?",
            ("settled-chief-child", chief_child["id"]),
        )
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("settled-chief-child", chief_child["id"]),
        )
    _send_owner_message(
        server, Principal(PrincipalKind.ticket, chief_child["id"]), "Chief child direct message"
    )
    chief_child_row = left.locator(
        f'.orchestrator-card.chief [data-ticket-id="{chief_child["id"]}"]'
    )
    expect(chief_child_row).to_be_visible(timeout=WAIT_MS)
    expect(chief_child_row.get_by_role("img", name="Unread reply")).to_have_count(1)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
    chief_child_row.click()
    expect(
        page.locator(f'[data-screen="ticket"][data-ticket-id="{chief_child["id"]}"]')
    ).to_be_visible()
    assert page.evaluate("location.hash").split("?", 1)[0] == f"#/workspace/{chief_child['id']}"
    assert settlement_writes == []
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM sprint_items WHERE settled=1").fetchone()[0] == 2
    shutil.copyfile(server.log_path, artifacts / "isolated-server.log")
