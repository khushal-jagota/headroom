"""Prove lifecycle controls across UI, real writers, SSE, and durable history.

Mocked frontend responses cannot establish that approval controls, persisted state,
and backlog state agree after a durable write and reload. This bounded synthetic
proof uses the existing deterministic conversation adapter and no provider calls.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.e2e.test_approval_groups_message_dots import _conversation, _message
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401
from tests.e2e.test_retired_page_interiors import _propose
from tests.support.principals import ticket_principal


def test_ticket_backlog_resume_and_settled_discovery(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    project = api.direct_post(
        server, "/api/projects", {"name": "Lifecycle proof", "priority": "P2"}
    )
    item = api.direct_post(
        server, "/api/items", {"title": "Synthetic lifecycle review", "project_id": project["id"]}
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Synthetic ticket with retained evidence",
            "worker_type": "coding",
            "brief": "A synthetic brief for browser verification.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    ticket_id = str(ticket["id"])
    artifact_href = f"/files/tickets/{ticket_id}/evidence.md"
    artifact = server.db_path.parent / "files" / "tickets" / ticket_id / "evidence.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "# Retained evidence\n\nThis file remains available while work is deferred."
    )
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    api.direct_patch(
        server,
        f"/api/tickets/{ticket_id}",
        {"field_values": {"brief": f"Approved brief with [retained evidence]({artifact_href})."}},
    )
    proposal = f"Pending success evidence survives deferral. [Original artifact]({artifact_href})"
    _propose(server, ticket_id, proposal)
    _conversation(server, "synthetic-lifecycle-conversation")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?",
            ("synthetic-lifecycle-conversation", ticket_id),
        )
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("synthetic-lifecycle-conversation", ticket_id),
        )

    transcript = "Conversation evidence remains readable after deferral."
    _message(server, "synthetic-lifecycle-conversation", ticket_principal(ticket_id), transcript)
    path = f"/api/tickets?detail=full&id={ticket_id}"
    before = api.get(server, path)
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{item['id']}/{ticket_id}",
        "[data-ticket-lifecycle]",
    )
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 560, "height": 1100})
    worker = page.locator('[data-screen="ticket"]')
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    expect(worker.locator("[data-ticket-retire-action]")).to_have_count(0)
    expect(worker.locator("[data-ticket-actions]")).to_have_count(0)
    with page.expect_response(
        lambda response: response.url.endswith(f"/{ticket_id}/backlog")
    ) as response:
        worker.locator("[data-ticket-backlog-action]").click()
    assert response.value.ok, response.value.text()
    deferred = api.get(server, path)
    assert deferred["is_backlogged"] is True
    assert deferred["stage"] == before["stage"]
    assert deferred["pending_proposal"] == before["pending_proposal"]
    assert deferred["conversation_id"] == before["conversation_id"]
    expect(worker.locator("[data-ticket-backlogged]")).to_be_visible()
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    page.reload()
    expect(worker.locator("[data-ticket-backlogged]")).to_be_visible()
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    page.goto(server.base + "/#/backlog")
    page.wait_for_function("location.hash === '#/workspace'", timeout=WAIT_MS)
    expect(page.locator("[data-backlog-tickets]")).to_have_count(0)
    page.goto(server.base + f"/#/workspace/item/{item['id']}")
    children = page.locator(f'[data-orchestrator-children="sprint_item:{item["id"]}"]')
    state_filter = children.get_by_label("Worker state", exact=True)
    state_filter.select_option("backlogged")
    deferred_row = children.locator(f'[data-sprint-ticket-id="{ticket_id}"]')
    expect(deferred_row).to_be_visible()
    expect(deferred_row).to_contain_text(ticket["title"])
    page.goto(server.base + f"/#/workspace/item/{item['id']}/{ticket_id}")
    expect(worker.locator("[data-ticket-backlogged]")).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(f"/{ticket_id}/resume")
    ) as response:
        worker.locator("[data-ticket-backlog-action]").click()
    assert response.value.ok, response.value.text()
    resumed = api.get(server, path)
    assert resumed["is_backlogged"] is False
    assert resumed["pending_proposal"] == before["pending_proposal"]
    expect(worker.locator("[data-ticket-backlog-action]")).to_have_text("Backlog ticket")
    page.reload()
    expect(worker.locator("[data-ticket-backlog-action]")).to_have_text("Backlog ticket")
    page.locator("[data-conversation-rest-bar]").click()
    page.locator('[data-conversation-lens-choice="full"]').click()
    expect(page.get_by_text(transcript, exact=True)).to_be_visible()

    api.direct_patch(server, f"/api/items/{item['id']}/settled", {"settled": True})
    page.goto(server.base + "/#/workspace")
    settled = page.locator(".settled-head")
    expect(settled).to_be_visible()
    settled.click()
    row = page.locator(f'[data-settled-item="{item["id"]}"]')
    expect(row).to_be_visible()
    row.locator(".settled-title").click()
    expect(page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')).to_be_visible()
