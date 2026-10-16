"""The supported catalog cannot remove historical Ticket and proposal access.

The real manifest, Ticket projection, and browser query cache share a connected
boundary. Mocked frontend manifests cannot prove that a migrated database still
serves original stage fields while each new-work selector uses the supported list.
The fixture restores historical definitions into an isolated database. No worker
runs, and no legacy Ticket stage or proposal receives an action.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle

RETIRED = {"personal", "planning-day", "planning-midday-check", "planning-sprint"}



def _historical_definitions(db: Path) -> None:
    fixture = Path(__file__).parents[1] / "support/retired_worker_types.json"
    definitions = json.loads(fixture.read_text())
    with sqlite3.connect(db) as conn:
        for position, definition in enumerate(definitions, 100):
            skill = definition["profile"]["specialist_skill"]
            conn.execute(
                "INSERT OR IGNORE INTO managed_skills VALUES (?,?,0)",
                (skill, f"---\nname: {skill}\ndescription: Historical fixture\n---\n"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO worker_types VALUES (?,?,?,0)",
                (definition["worker_type"], position, json.dumps(definition)),
            )

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
    tmp_path: Path,
) -> None:
    server = server_factory(
        seed_db=_historical_definitions,
        trusted_ingress_env={"PLAN_DISPATCH_ENABLED": "0"},
    )
    evidence = Path(os.environ.get("PANELS_CATALOG_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    ticket = api.direct_post(server, "/api/tickets", {
        "title": "Historical personal outcome remains reviewable",
        "worker_type": "coding", "kickoff_note": "Original personal brief",
        "orchestrator": {"kind": "chief", "id": "chief"},
    })
    ticket_id = ticket["id"]
    planning = api.direct_post(server, "/api/tickets", {
        "title": "Historical planning review remains readable",
        "worker_type": "coding", "kickoff_note": "Original planning review",
        "orchestrator": {"kind": "chief", "id": "chief"},
    })
    with sqlite3.connect(server.db_path) as conn:
        proposal = json.loads(conn.execute(
            "SELECT pending_proposal FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()[0])
        proposal.update(field="outcome", body="Original personal outcome proposal")
        conn.execute(
            "UPDATE tickets SET worker_type='personal',stage='needs_outcome',"
            "ceiling='needs_outcome',field_values=?,pending_proposal=? WHERE id=?",
            (json.dumps({"brief": "Original personal brief"}), json.dumps(proposal), ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET worker_type='planning-day',stage='cancelled',"
            "ceiling='needs_review',field_values=?,pending_proposal=NULL WHERE id=?",
            (json.dumps({
                "review": "Original planning review", "retirement": "Reviewed disposition",
            }), planning["id"]),
        )
    page = open_page(context_factory(), server, "#/config", "[data-workers-list]")
    page.set_default_timeout(WAIT_MS)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def screenshots(name: str) -> None:
        for width in (1440, 560):
            page.set_viewport_size({"width": width, "height": 1100})
            if name == "historical-personal-ticket":
                page.keyboard.press("Escape")
                page.keyboard.press("Escape")
                expect(page.locator("[data-conversation-pane]")).to_have_attribute(
                    "data-conversation-state", "rest"
                )
                expect(page.locator("[data-feedback-page-title]")).to_be_visible()
            page.screenshot(
                path=str(evidence / f"{name}-{width}.png"), full_page=True,
                animations="disabled",
            )
        page.set_viewport_size({"width": 1440, "height": 1100})

    destinations = page.locator("[data-worker-destination]")
    expect(destinations).not_to_have_count(0)
    for worker_type in RETIRED:
        expect(page.locator(f'[data-worker-destination][href$="/{worker_type}"]')).to_have_count(0)
    screenshots("supported-worker-catalog")
    page.goto(server.base + "/#/backlog")
    page.locator('[data-create="ticket"] > summary').click()
    creation = page.locator('[data-input="worker-type"]')
    expect(creation.locator('option[value="coding"]')).to_have_count(1)
    for worker_type in RETIRED:
        expect(creation.locator(f'option[value="{worker_type}"]')).to_have_count(0)
    historical_filter = page.get_by_role("combobox", name="Filter worker type", exact=True)
    expect(historical_filter.locator('option[value="personal"]')).to_have_count(1)
    ticket_choices = creation.locator("option").evaluate_all(
        "options => options.map(option => ({value: option.value, label: option.textContent}))"
    )
    screenshots("supported-ticket-creation")
    historical_filter.select_option("personal")
    expect(page.locator(f'[data-ticket-id="{ticket_id}"]')).to_be_visible()

    page.goto(server.base + "/#/scheduled-tasks")
    page.locator("[data-create-schedule]").click()
    schedule_type = page.locator('[data-input="worker-type"]')
    expect(schedule_type.locator('option[value="coding"]')).to_have_count(1)
    for worker_type in RETIRED:
        expect(schedule_type.locator(f'option[value="{worker_type}"]')).to_have_count(0)
    schedule_choices = schedule_type.locator("option").evaluate_all(
        "options => options.map(option => ({value: option.value, label: option.textContent}))"
    )
    screenshots("supported-schedule-creation")
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
    screenshots("historical-personal-schedule")

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
    worker.locator('[data-field="brief"] > summary').click()
    expect(worker.get_by_text("Original personal brief", exact=True)).to_be_visible()
    screenshots("historical-personal-ticket")
    page.goto(server.base + "/#/review")
    review = page.locator(f'[data-review-card][data-ticket-id="{ticket_id}"]')
    expect(review).to_have_attribute("data-field", "outcome")
    expect(review).to_contain_text("Original personal outcome proposal")
    expect(page.locator("[data-review-manifest-error]")).to_have_count(0)
    screenshots("historical-personal-proposal")
    review.locator("[data-open-ticket]").click()
    expect(page.locator('[data-screen="ticket"]')).to_have_attribute("data-stage", "needs_outcome")
    page.goto(server.base + f"/#/ticket/{planning['id']}")
    historical_planning = page.locator('[data-screen="ticket"]')
    expect(historical_planning).to_have_attribute("data-stage", "cancelled")
    expect(page.locator("[data-ticket-manifest-error]")).to_have_count(0)
    # Original fields remain accessible through the existing stage folds.
    original_review = historical_planning.get_by_text("Original planning review", exact=True)
    if not original_review.is_visible():
        historical_planning.locator("[data-stage-fold-open]").first.click()
        historical_planning.locator(
            '[data-field="review"] > summary'
        ).click()
    expect(original_review).to_be_visible()
    screenshots("historical-planning-ticket")
    assert errors == []
    after = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert after["worker_type"] == "personal" and after["stage"] == "needs_outcome"
    assert after["pending_proposal"]["body"] == "Original personal outcome proposal"
    (evidence / "connected-catalog-proof.json").write_text(json.dumps({
        "isolation": "Synthetic historical definitions and Ticket; dispatch disabled",
        "source_build_hashes": {
            str(path.relative_to(Path(__file__).parents[2])): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for root in ("web/src", "web/dist")
            for path in sorted((Path(__file__).parents[2] / root).rglob("*"))
            if path.is_file()
        },
        "personal_daily_schedule": "Retired; controls disabled; no Enable or Save action",
        "viewports": [1440, 560], "page_errors": errors,
        "asserted_absent_from_each_creation_selector": sorted(RETIRED),
        "ticket_creation_choices": ticket_choices, "schedule_creation_choices": schedule_choices,
        "preserved_stage": after["stage"], "preserved_proposal": after["pending_proposal"]["field"],
    }, indent=2))

    (evidence / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Connected catalog evidence</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:16px}"
        "img{max-width:100%;border:1px solid #ccc}section{margin:40px 0}</style>"
        "<h1>Connected catalog and historical access</h1>"
        "<p>Isolated synthetic database. No historical Ticket action. Dispatch disabled.</p>"
        "<p><a href='connected-catalog-proof.json'>"
        "Assertions, exact options, source and build hashes</a></p>"
        + "".join(
            f"<section><h2>{path.stem}</h2><a href='{path.name}'>"
            f"<img src='{path.name}' alt='{path.stem}'></a></section>"
            for path in sorted(evidence.glob("*.png"))
        )
    )
