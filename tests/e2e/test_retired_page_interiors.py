"""Prove a browser revision preserves exact feedback and refreshes its real page.

The actual reject request, durable feedback and mounted page must agree. The isolated
conversation adapter records newly started conversations without provider prompts.
Exact presentation values and duplicate approval/artifact flows belong below this boundary.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_approval_groups_message_dots import _conversation
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401

# pytest resolves this explicitly imported fixture by name.


def _propose(server: ServerHandle, ticket_id: str, body: str) -> None:
    response = httpx.post(
        server.base + f"/api/tickets/{ticket_id}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
        json={"body": body, "recap": "The next result is ready for review."},
        timeout=10,
    )
    assert response.is_success, response.text


def test_page_revision_persists_exact_feedback_and_refreshes(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    project = api.direct_post(server, "/api/projects", {"name": "Page proof"})
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Verify the complete retirement product",
            "project_id": project["id"],
            "body": "Review the real connected product before release.",
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Verify approval and revision on the approved Worker page",
            "worker_type": "coding",
            "brief": "The brief is ready for review.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    unrelated_project = api.direct_post(
        server, "/api/projects", {"name": "Unrelated proof"}
    )
    unrelated = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Worker from a different project",
            "worker_type": "coding",
            "project_id": unrelated_project["id"],
            "brief": "Not in the selected project.",
            "orchestrator": {"kind": "chief", "id": "chief"},
        },
    )
    ticket_id = str(ticket["id"])
    _conversation(server, "page-worker")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?", ("page-worker", ticket_id))
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("page-worker", ticket_id),
        )
    api.direct_patch(
        server,
        f"/api/tickets/{ticket_id}",
        {
            "recap": "Verify approval and conversation boundaries in the new document.",
        },
    )
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{item['id']}/{ticket_id}",
        '[data-screen="ticket"]',
    )
    page.set_default_timeout(WAIT_MS)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    requests: list[JsonObject] = []
    page.on(
        "request",
        lambda request: (
            requests.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "body": request.post_data_json,
                }
            )
            if request.method in {"POST", "PATCH"} and "/api/tickets/" in request.url
            else None
        ),
    )
    worker = page.locator('[data-screen="ticket"]')
    rail_row = page.locator(f'.board-workspace-left [data-ticket-id="{ticket_id}"]')
    expect(rail_row).to_be_visible()
    expect(rail_row.locator(".stage-mark")).to_have_count(0)
    expect(worker.locator("[data-send-back]")).to_have_count(0)
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    _propose(server, ticket_id, "The product preserves the actual approval contract.")
    owner_proposal = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert owner_proposal["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert owner_proposal["pending_proposal"]["field"] == "success_condition"
    expect(rail_row).to_be_visible()
    expect(rail_row.locator(".stage-mark")).to_have_count(0)
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    worker.locator("[data-send-back]").click()
    revision = "Preserve exact conversation dots and prove the saved reviewer."
    worker.locator("[data-revision-note]").fill(revision)
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith(f"/{ticket_id}/reject")
        )
    ) as rejected:
        worker.locator("[data-revision-send]").click()
    assert rejected.value.ok, rejected.value.text()
    canonical = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert canonical["stage"] == "needs_success_condition"
    assert canonical["pending_proposal"] is None
    with sqlite3.connect(server.db_path) as conn:
        feedback = conn.execute(
            "SELECT stage, feedback_json FROM ticket_revision_feedback WHERE ticket_id=?",
            (ticket_id,),
        ).fetchone()
    assert feedback is not None and feedback[0] == "needs_success_condition"
    assert json.loads(feedback[1])[0]["message"] == revision
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    page.reload()
    expect(worker).to_have_attribute("data-stage", "needs_success_condition")
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    page.goto(server.base + f"/#/workspace/item/{item['id']}")
    pane_selector = f'[data-sprint-item-workspace="{item["id"]}"]'
    pane = page.locator(pane_selector)
    expect(pane).to_be_visible()
    expect(pane.locator('[data-workspace-group="awaiting_reply"]')).to_have_count(0)
    expect(pane.locator('[data-workspace-section="today"]')).to_have_count(0)
    # The project remains a fact after the standalone Workers page is removed.
    expect(pane.locator(".page-eyebrow > span").first).to_have_text("Page proof")
    expect(pane.locator('a[href*="#/backlog"]')).to_have_count(0)
    children = pane.locator(f'[data-orchestrator-children="sprint_item:{item["id"]}"]')
    expect(children.locator(f'[data-sprint-ticket-id="{ticket_id}"]')).to_be_visible()
    expect(children.locator(f'[data-sprint-ticket-id="{unrelated["id"]}"]')).to_have_count(0)
    page.goto(server.base + "/#/workspace/chief-of-staff")
    chief_children = page.locator('[data-orchestrator-children="chief:chief"]')
    expect(chief_children.locator(f'[data-sprint-ticket-id="{unrelated["id"]}"]')).to_be_visible()
    expect(chief_children.locator(f'[data-sprint-ticket-id="{ticket_id}"]')).to_have_count(0)
    assert errors == [], errors
    assert console_errors == [], console_errors
    assert len([request for request in requests if request["url"].endswith("/reject")]) == 1
