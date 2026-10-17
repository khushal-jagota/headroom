"""Prove lifecycle controls across UI, real writers, SSE, and durable history.

Mocked frontend responses cannot establish that approval controls, persisted state,
and cancelled history agree after a durable write and reload. This bounded synthetic
proof uses the existing deterministic conversation adapter and no provider calls.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.e2e.test_approval_groups_message_dots import _conversation, _message
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401
from tests.e2e.test_retired_page_interiors import _propose
from tests.support.principals import ticket_principal


def _screenshot(page: Page, evidence: Path, name: str) -> None:
    # Record the settled visual state, rather than an entry animation frame.
    page.evaluate("""async () => {
      await document.fonts.ready;
      await Promise.all(document.getAnimations().map(
        animation => animation.finished.catch(() => {})));
    }""")
    page.screenshot(path=str(evidence / name), full_page=True)


def test_ticket_backlog_resume_cancelled_history_and_settled_discovery(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    evidence = server.db_path.parent / "lifecycle-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
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
    artifact.write_text("# Retained evidence\n\nThis file remains in cancelled history.")
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
    proposal = f"Pending success evidence stays historical. [Original artifact]({artifact_href})"
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

    transcript = "Retained conversation evidence remains readable after cancellation."
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
    page.set_viewport_size({"width": 1440, "height": 1100})
    worker = page.locator('[data-screen="ticket"]')
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    expect(worker.locator("[data-ticket-retire-action]")).to_have_count(0)
    expect(worker.locator("[data-ticket-actions]")).to_have_count(0)
    _screenshot(page, evidence, "ticket-active-desktop.png")
    page.set_viewport_size({"width": 560, "height": 1100})
    _screenshot(page, evidence, "ticket-active-compact.png")
    page.set_viewport_size({"width": 1440, "height": 1100})
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
    _screenshot(page, evidence, "ticket-backlogged-desktop.png")
    page.set_viewport_size({"width": 560, "height": 1100})
    _screenshot(page, evidence, "ticket-backlogged-compact.png")
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
    for width, label in ((1440, "desktop"), (560, "compact")):
        page.set_viewport_size({"width": width, "height": 1100})
        _screenshot(page, evidence, f"backlog-filter-list-{label}.png")
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
    # The retained cancelled record is synthetic fixture history, not a live action.
    reason = "Historical cancellation: preserve its evidence without success."
    with sqlite3.connect(server.db_path) as conn:
        conn.row_factory = sqlite3.Row
        original = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
        values = json.loads(original["field_values"])
        values["retirement"] = json.dumps(
            {
                "reason": reason,
                "original_record": original,
                "original_stage": original["stage"],
                "original_pending_proposal_json": original["pending_proposal"],
            }
        )
        conn.execute(
            "UPDATE tickets SET stage='cancelled',pending_proposal=NULL,field_values=? WHERE id=?",
            (json.dumps(values), ticket_id),
        )
        stored_history = dict(
            conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        )
    page.reload()
    retired = api.get(server, path)
    assert retired["stage"] == "cancelled"
    assert retired["pending_proposal"] is None
    assert retired["field_values"]["brief"] == before["field_values"]["brief"]
    provenance = json.loads(retired["field_values"]["retirement"])
    assert provenance["reason"] == reason
    assert provenance["original_stage"] == before["stage"]
    assert json.loads(provenance["original_pending_proposal_json"])["body"] == proposal
    assert retired["conversation_id"] == before["conversation_id"]
    expect(worker.locator("[data-retirement-reason]")).to_have_text(reason)
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    expect(worker.locator("[data-ticket-retire-action]")).to_have_count(0)
    expect(worker.locator("[data-worker-live-card]")).to_have_count(0)
    page.reload()
    expect(worker.locator("[data-ticket-cancelled]")).to_have_text("Cancelled")
    worker.locator("[data-stage-fold-open]").click()
    for summary in worker.locator(".disclosure--stage > summary").all():
        summary.click()
    expect(worker).to_contain_text("Historical proposal (not approved)")
    expect(worker).to_contain_text("Pending success evidence stays historical")
    artifact_links = worker.locator('[data-field="success_condition"] a[href*="preview"]')
    expect(artifact_links.first).to_be_visible()
    artifact_links.first.click()
    expect(page.locator("[data-ticket-artifact]")).to_contain_text(
        "This file remains in cancelled history."
    )
    for width, label in ((1440, "desktop"), (560, "compact")):
        page.set_viewport_size({"width": width, "height": 1100})
        expect(page.locator("[data-ticket-artifact]")).to_contain_text(
            "This file remains in cancelled history."
        )
        _screenshot(page, evidence, f"ticket-cancelled-artifact-{label}.png")
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 560, "height": 1100})
    _screenshot(page, evidence, "ticket-cancelled-history-compact.png")
    page.set_viewport_size({"width": 1440, "height": 1100})
    _screenshot(page, evidence, "ticket-cancelled-history-desktop.png")
    page.locator("[data-conversation-rest-bar]").click()
    page.locator('[data-conversation-lens-choice="full"]').click()
    expect(page.get_by_text(transcript, exact=True)).to_be_visible()
    for width, label in ((1440, "desktop"), (560, "compact")):
        page.set_viewport_size({"width": width, "height": 1100})
        expect(page.get_by_text(transcript, exact=True)).to_be_visible()
        _screenshot(page, evidence, f"ticket-cancelled-conversation-{label}.png")
    with sqlite3.connect(server.db_path) as conn:
        stored = conn.execute(
            "SELECT stage,is_backlogged,conversation_id FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()
    assert stored == ("cancelled", 0, "synthetic-lifecycle-conversation")
    with sqlite3.connect(server.db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert (
            dict(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
            == stored_history
        )

    page.goto(server.base + "/#/backlog")
    page.wait_for_function("location.hash === '#/workspace'", timeout=WAIT_MS)
    page.goto(server.base + f"/#/workspace/item/{item['id']}")
    children = page.locator(f'[data-orchestrator-children="sprint_item:{item["id"]}"]')
    state_filter = children.get_by_label("Worker state", exact=True)
    expect(state_filter).to_have_value("active")
    expect(children.locator(f'[data-sprint-ticket-id="{ticket_id}"]')).to_have_count(0)
    state_filter.select_option(label="Cancelled")
    retired_group = children.locator('[data-workspace-group="cancelled"]')
    expect(retired_group).to_be_attached()
    if not retired_group.evaluate("element => element.open"):
        retired_group.locator(":scope > summary").click()
    retired_row = retired_group.locator(f'[data-sprint-ticket-id="{ticket_id}"]')
    expect(retired_row).to_be_visible()
    expect(retired_group.locator("[data-group-count]")).to_have_text("1")
    expect(retired_row).to_have_attribute("href", f"#/workspace/item/{item['id']}/{ticket_id}")
    for width, label in ((1440, "desktop"), (560, "compact")):
        page.set_viewport_size({"width": width, "height": 1100})
        _screenshot(page, evidence, f"cancelled-filter-list-{label}.png")
    retired_row.click()
    expect(worker.locator("[data-retirement-reason]")).to_have_text(reason)

    api.direct_patch(server, f"/api/items/{item['id']}/settled", {"settled": True})
    page.goto(server.base + "/#/workspace")
    settled = page.locator(".settled-head")
    expect(settled).to_be_visible()
    settled.click()
    row = page.locator(f'[data-settled-item="{item["id"]}"]')
    expect(row).to_be_visible()
    for width, label in ((1440, "desktop"), (560, "compact")):
        page.set_viewport_size({"width": width, "height": 1100})
        _screenshot(page, evidence, f"settled-discovery-{label}.png")
    page.set_viewport_size({"width": 1440, "height": 1100})
    row.locator(".settled-title").click()
    expect(page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')).to_be_visible()
    (evidence / "lifecycle-browser-evidence.json").write_text(
        json.dumps(
            {
                "ticket_id": ticket_id,
                "stage_before": before["stage"],
                "cancelled_stage": retired["stage"],
                "preserved_pending_proposal": provenance["original_pending_proposal_json"],
                "conversation_read": transcript,
                "isolation": (
                    "temporary rail-proof.db with deterministic conversation adapter, "
                    "no provider calls"
                ),
                "settled_discovery": "existing Workspace Settled fold",
            },
            indent=2,
        )
    )
