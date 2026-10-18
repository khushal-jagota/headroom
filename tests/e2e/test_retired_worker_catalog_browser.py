"""The supported catalog cannot remove historical Ticket and proposal access.

The real manifest, Ticket projection, and browser query cache share a connected
boundary. Mocked frontend manifests cannot prove that a migrated database still
serves original stage fields while each new-work selector uses the supported list.
The fixture restores historical definitions into an isolated database. No worker
runs, and no legacy Ticket stage or proposal receives an action.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.retired_workers import seed_historical_worker_type

RETIRED = {"personal", "planning-day", "planning-midday-check", "planning-sprint"}


def _historical_definitions(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for worker_type in sorted(RETIRED):
            seed_historical_worker_type(conn, worker_type)

        conn.execute(
            "INSERT INTO scheduled_ticket_schedules "
            "(id,enabled,cadence,local_time,title,worker_type,priority,created_at,updated_at) "
            "VALUES ('historical-personal-daily',0,'daily','09:00',"
            "'Historical daily personal task','personal','P2',0,0)"
        )


def test_supported_catalog_preserves_historical_connected_pages(
    server_factory: Callable[..., ServerHandle],
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = server_factory(
        seed_db=_historical_definitions,
        trusted_ingress_env={"PLAN_DISPATCH_ENABLED": "0"},
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Historical personal outcome remains reviewable",
            "worker_type": "coding",
            "brief": "Original personal brief",
            "orchestrator": {"kind": "chief", "id": "chief"},
        },
    )
    ticket_id = ticket["id"]
    with sqlite3.connect(server.db_path) as conn:
        proposal = json.loads(
            conn.execute(
                "SELECT pending_proposal FROM tickets WHERE id=?", (ticket_id,)
            ).fetchone()[0]
        )
        proposal.update(field="outcome", body="Original personal outcome proposal")
        conn.execute(
            "UPDATE tickets SET worker_type='personal',stage='needs_outcome',"
            "ceiling='needs_outcome',field_values=?,pending_proposal=? WHERE id=?",
            (json.dumps({"brief": "Original personal brief"}), json.dumps(proposal), ticket_id),
        )
    page = open_page(context_factory(), server, "#/config", "[data-workers-list]")
    page.set_default_timeout(WAIT_MS)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    destinations = page.locator("[data-worker-destination]")
    expect(destinations).not_to_have_count(0)
    for worker_type in RETIRED:
        expect(page.locator(f'[data-worker-destination][href$="/{worker_type}"]')).to_have_count(0)
    supported_catalog = api.get(server, "/api/worker-types?supported=true")["worker_types"]
    supported_names = {entry["worker_type"] for entry in supported_catalog}
    assert "coding" in supported_names
    assert not RETIRED & supported_names
    page.goto(server.base + "/#/workspace/chief-of-staff")
    chief_children = page.locator('[data-orchestrator-children="chief:chief"]')
    expect(chief_children.get_by_label("Worker state")).to_have_value("active")
    personal_row = chief_children.locator(f'[data-sprint-ticket-id="{ticket_id}"]')
    expect(personal_row).to_be_visible()
    expect(personal_row).to_contain_text(ticket["title"])
    page.goto(server.base + "/#/scheduled-tasks")
    page.locator("[data-create-schedule]").click()
    schedule_type = page.locator('[data-input="worker-type"]')
    expect(schedule_type.locator('option[value="coding"]')).to_have_count(1)
    for worker_type in RETIRED:
        expect(schedule_type.locator(f'option[value="{worker_type}"]')).to_have_count(0)
    page.locator("[data-back-to-schedules]").click()
    personal_schedule = page.locator('[data-schedule-id="historical-personal-daily"]')
    expect(personal_schedule.locator("[data-schedule-toggle]")).to_be_disabled()
    expect(personal_schedule.get_by_role("switch", name="Enable schedule")).to_have_count(0)
    personal_schedule.locator("[data-schedule-title]").click()
    expect(page.locator("[data-retired-schedule]")).to_contain_text("Retired schedule")
    expect(page.locator("[data-retired-schedule]")).to_contain_text("Every day")
    expect(page.locator('[data-input="worker-type"]')).to_be_disabled()
    expect(page.locator('[data-input="enabled"]')).to_be_disabled()
    expect(page.get_by_role("switch", name="Enable schedule")).to_have_count(0)
    expect(page.locator("[data-commit-schedule]")).to_have_count(0)

    page.goto(server.base + f"/#/ticket/{ticket_id}")
    worker = page.locator('[data-screen="ticket"]')
    expect(worker).to_have_attribute("data-stage", "needs_outcome")
    expect(worker).to_contain_text("Original personal outcome proposal")
    expect(page.locator("[data-ticket-manifest-error]")).to_have_count(0)
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    expect(page.locator("[data-conversation-pane]")).to_have_attribute(
        "data-conversation-state", "rest"
    )
    worker.locator("[data-stage-fold-open]").click()
    brief_group = worker.locator('[data-field="brief"]')
    if not brief_group.evaluate("element => element.open"):
        brief_group.locator(":scope > summary").click()
    expect(worker.get_by_text("Original personal brief", exact=True)).to_be_visible()
    page.goto(server.base + "/#/review")
    review = page.locator(f'[data-review-card][data-ticket-id="{ticket_id}"]')
    expect(review).to_have_attribute("data-field", "outcome")
    expect(review).to_contain_text("Original personal outcome proposal")
    expect(page.locator("[data-review-manifest-error]")).to_have_count(0)
    review.locator("[data-open-ticket]").click()
    expect(page.locator('[data-screen="ticket"]')).to_have_attribute("data-stage", "needs_outcome")
    assert errors == []
    after = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert after["worker_type"] == "personal" and after["stage"] == "needs_outcome"
    assert after["pending_proposal"]["body"] == "Original personal outcome proposal"
