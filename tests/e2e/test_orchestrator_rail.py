"""Prove rail writes, invalidation and navigation against an isolated live server.

Settle/Reopen can hide outstanding work or replace the open Worker if the HTTP write,
board projection, SSE invalidation and route selection disagree. Frontend mocks and
backend tests cannot prove those boundaries together. Helper tests own grouping rules;
this small browser proof owns the connected behavior and keyboard/pointer access.
No conversation sends occur: deterministic recorded events provide unread evidence.
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
