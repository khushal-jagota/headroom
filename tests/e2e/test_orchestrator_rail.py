"""Prove rail writes, invalidation and navigation against an isolated live server.

Settle/Reopen can hide outstanding work or replace the open Worker if the HTTP write,
board projection, SSE invalidation and route selection disagree. Frontend mocks and
backend tests cannot prove those boundaries together. Helper tests own grouping rules;
this small browser proof owns the connected behavior and keyboard/pointer access.
No backend prompts occur: deterministic durable messages provide unread evidence.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import (
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
    _append_rows,
    capture_success,
)
from tests.support.connected_rows import _conversation, _message
from tests.support.server_fixtures import rail_server as rail_server

from planner.conversation.events import UserInputQuestion, UserInputRequestedEventPayload
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind

EVIDENCE = Path("/home/vps/Deployments/Panels/current/data/files/tickets/t_9fh1vzxc/artifacts/rail")
LONG_TITLE = "Review the entire Worker title when the adjustable rail clips this long approval row"




def _seed(server: ServerHandle, api: ApiHelper) -> tuple[JsonObject, JsonObject, JsonObject]:
    project = api.direct_post(server, "/api/projects", {"name": "Rail proof"})
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Review the connected retirement rail",
            "project_id": project["id"],
        },
    )
    empty = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Empty orchestrator stays visible",
            "project_id": project["id"],
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": LONG_TITLE,
            "worker_type": "coding",
            "brief": "Review the brief.",
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
                "brief": "Agreed brief.",
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
            "brief": "Agreed brief.",
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
    expect(card).to_be_visible(timeout=WAIT_MS)
    expect(left.locator(".orchestrator-card").first).to_have_class(re.compile(r"\bchief\b"))
    expect(
        left.locator(".orchestrator-card.chief").get_by_role("button", name="Settle", exact=False)
    ).to_have_count(0)
    expect(card.locator(".orchestrator-eyebrow")).to_contain_text("Rail proof")
    expect(left.locator(f'[data-sprint-item="{empty["id"]}"]')).to_be_visible()
    expect(row.get_by_role("img", name="Unread message")).to_be_visible(timeout=WAIT_MS)
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
    # The retired global list redirects to Workspace. Settled parents retain child access.
    page.goto(server.base + "/#/backlog?project=" + item["project_id"])
    page.wait_for_function("location.hash === '#/workspace'", timeout=WAIT_MS)
    expect(page.locator("[data-backlog-tickets]")).to_have_count(0)
    left.get_by_role("button", name="Settled", exact=False).click()
    scoped_parent = left.locator(f'[data-settled-item="{item["id"]}"]')
    scoped_parent.locator(".settled-title").click()
    scoped_pane = page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
    scoped_row = scoped_pane.locator(f'[data-sprint-ticket-id="{ticket["id"]}"]')
    # An absent row can mean loading, so inspect the mounted fold before toggling it.
    scoped_group = scoped_pane.locator('[data-workspace-group="awaiting_approval"]')
    expect(scoped_group).to_be_visible(timeout=WAIT_MS)
    if scoped_group.get_attribute("open") is None:
        scoped_group.locator(":scope > summary").click()
    expect(scoped_row).to_be_visible(timeout=WAIT_MS)
    expect(scoped_row).to_contain_text(LONG_TITLE)
    scoped_row.click()
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    page.go_back()
    expect(scoped_row).to_be_visible(timeout=WAIT_MS)
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
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    capture_success(page, str(EVIDENCE / "rail-1440.png"))
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
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    api.direct_post(
        server, "/api/conversation/conversations/rail-chief/owner-read", {"through_sequence": 1}
    )
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0, timeout=WAIT_MS)
    expect(card.locator(".item-head .stage-mark")).to_be_visible()
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    capture_success(page, str(EVIDENCE / "own-conversation-marks.png"))
