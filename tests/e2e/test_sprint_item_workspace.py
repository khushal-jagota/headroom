"""The real Sprint Item route composes its workspace, transcript, and live updates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def _post(server: ServerHandle, path: str, body: JsonObject) -> JsonObject:
    response = httpx.post(server.base + path, json=body, timeout=10.0)
    assert response.status_code < 300, response.text
    value: JsonObject = response.json()
    return value


def _store_supervisor_conversation(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    conversation_id: str,
    created_at: int,
    reply: str,
) -> None:
    identity = json.dumps(
        [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", item_id]]
    )
    conn.execute(
        "INSERT INTO conversations (conversation_id,backend_key,model,workspace_folder,"
        "identity_environment_variables,access,latest_sequence,created_at) "
        "VALUES (?,'codex','gpt-5.6-sol','/tmp/workspace',?,'full',2,?)",
        (conversation_id, identity, created_at),
    )
    conn.executemany(
        "INSERT INTO conversation_events "
        "(conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)",
        (
            (
                conversation_id,
                1,
                "agent_message",
                json.dumps({"text": reply}),
                created_at + 1,
            ),
            (
                conversation_id,
                2,
                "turn_ended",
                json.dumps({"ending": "completed", "error_summary": None}),
                created_at + 2,
            ),
        ),
    )


def test_sprint_item_workspace_real_route_is_responsive_live_and_keeps_history(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    project = _post(
        server,
        "/api/projects",
        {"name": "Workspace project", "summary": "", "priority": "P2"},
    )
    sprint = _post(
        server,
        "/api/sprints",
        {"name": "Workspace sprint", "date_start": "2026-07-01", "date_end": "2026-07-07"},
    )
    item = _post(
        server,
        "/api/items",
        {
            "title": "Supervise the outcome",
            "body": "The shared brief stays editable and live.",
            "priority": "P2",
            "project_id": project["id"],
        },
    )
    committed = httpx.put(
        f"{server.base}/api/collections/sprint_outcomes/{sprint['id']}/{item['id']}", timeout=10.0
    )
    assert committed.status_code < 300, committed.text
    today_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Work today",
            "worker_type": "coding",
            "kickoff_note": "Start.",
            "sprint_item_id": item["id"],
            "sprint_id": sprint["id"],
        },
    )
    review_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Review off today",
            "worker_type": "coding",
            "kickoff_note": "Start.",
            "sprint_item_id": item["id"],
            "sprint_id": sprint["id"],
        },
    )
    backlog_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Keep the Backlog Ticket name visible",
            "worker_type": "coding",
            "kickoff_note": "Start.",
            "sprint_item_id": item["id"],
        },
    )
    removed = httpx.delete(
        f"{server.base}/api/collections/day_tickets/today/{review_ticket['id']}", timeout=10.0
    )
    assert removed.status_code < 300, removed.text
    api.direct_post(
        server,
        f"/api/tickets/{review_ticket['id']}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    proposed = httpx.post(
        f"{server.base}/api/tickets/{review_ticket['id']}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(review_ticket["id"])},
        json={"recap": "Ready for review", "body": "The outcome is proven."},
        timeout=10.0,
    )
    assert proposed.status_code < 300, proposed.text
    artifact = httpx.put(
        f"{server.base}/files/sprint-items/{item['id']}/artifacts/proof.md",
        headers={
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": str(item["id"]),
        },
        json={"content": "# Proof"},
        timeout=10.0,
    )
    assert artifact.status_code < 300, artifact.text
    for index in range(6):
        extra = httpx.put(
            f"{server.base}/files/sprint-items/{item['id']}/artifacts/evidence-{index}.txt",
            headers={
                "X-Plan-Actor": "sprint_item_supervisor",
                "X-Plan-Sprint-Item-ID": str(item["id"]),
            },
            json={"content": str(index)},
            timeout=10.0,
        )
        assert extra.status_code < 300, extra.text
    api.direct_patch(
        server,
        f"/api/items/{item['id']}",
        {"body": f"[Open proof](/files/sprint-items/{item['id']}/artifacts/proof.md)"},
    )

    past_id = "conv_workspace_past"
    active_id = "conv_workspace_active"
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET worker_step_claim = 'out', pending_proposal = NULL, "
            "sprint_id = NULL WHERE id = ?",
            (backlog_ticket["id"],),
        )
        _store_supervisor_conversation(
            conn,
            item_id=str(item["id"]),
            conversation_id=past_id,
            created_at=1_700_000_000,
            reply="Past supervisor marker",
        )
        _store_supervisor_conversation(
            conn,
            item_id=str(item["id"]),
            conversation_id=active_id,
            created_at=1_700_000_100,
            reply="Current supervisor marker",
        )
        conn.execute(
            "UPDATE agents SET conversation_id=? WHERE agent_key=?",
            (active_id, item["supervisor"]["agent_key"]),
        )

    page = open_page(
        context_factory(),
        server,
        f"#/sprint?item={item['id']}",
        f'[data-sprint-item-view="{item["id"]}"]',
    )
    page.locator(f'[data-sprint-ticket-id="{today_ticket["id"]}"]').wait_for(timeout=WAIT_MS)
    backlog_row = page.locator(f'[data-sprint-ticket-id="{backlog_ticket["id"]}"]')
    backlog_title = backlog_row.get_by_text("Keep the Backlog Ticket name visible", exact=True)
    backlog_placement = backlog_row.locator("[data-ticket-backlog]")
    backlog_title.wait_for(timeout=WAIT_MS)
    backlog_placement.wait_for(state="attached", timeout=WAIT_MS)
    assert backlog_placement.text_content() == "Backlog"
    assert backlog_row.get_attribute("data-ticket-state") == "current-assigned"
    assert backlog_row.get_attribute("href") == (
        f"#/workspace/item/{item['id']}/{backlog_ticket['id']}"
    )
    assert backlog_row.locator("xpath=ancestor::details[1]").get_attribute(
        "data-workspace-group"
    ) == "current-assigned"
    desktop_geometry = page.evaluate(
        """([rowSelector]) => {
            const row = document.querySelector(rowSelector);
            const title = row.querySelector('.ticket-row-title').getBoundingClientRect();
            const placement = row.querySelector('[data-ticket-backlog]').getBoundingClientRect();
            return { titleWidth: title.width, titleTop: title.top, placementTop: placement.top };
        }""",
        [f'[data-sprint-ticket-id="{backlog_ticket["id"]}"]'],
    )
    assert desktop_geometry["titleWidth"] > 200, desktop_geometry
    assert abs(desktop_geometry["titleTop"] - desktop_geometry["placementTop"]) < 2
    # Remaining Tickets starts collapsed, and its rows carry the shared row grammar:
    # the condition is the stage mark's label, not a separate word, and the word is the
    # heading the row sits under, lowercased.
    remaining = page.locator('[data-workspace-section="remaining"]')
    assert remaining.get_attribute("open") is None
    remaining.locator("> summary").click()
    off_today = page.locator(f'[data-sprint-ticket-id="{review_ticket["id"]}"]')
    assert off_today.get_attribute("data-ticket-state") == "current-awaiting-approval"
    off_today.get_by_label("needs your approval").wait_for(timeout=WAIT_MS)
    artifacts = page.locator("[data-artifact-strip]")
    artifacts.wait_for(timeout=WAIT_MS)
    assert artifacts.locator("[data-artifact-chip]").count() == 7
    assert artifacts.get_by_role("button", name="+2 more").is_visible()
    artifacts.get_by_role("button", name="+2 more").click()
    assert artifacts.get_by_role("button", name="Show fewer").is_visible()

    brief_link = page.get_by_role("link", name="Open proof")
    brief_link.focus()
    brief_link.click()
    preview = page.locator("[data-ticket-artifact]")
    preview.wait_for(timeout=WAIT_MS)
    assert preview.evaluate("element => document.activeElement === element") is True
    assert page.locator("[data-conversation-input]").is_visible()
    preview.locator("[data-ticket-artifact-close]").click()
    preview.wait_for(state="detached", timeout=WAIT_MS)

    proof_chip = artifacts.locator('[data-artifact-chip="artifacts/proof.md"]')
    proof_chip.focus()
    proof_chip.click()
    preview.wait_for(timeout=WAIT_MS)
    page.keyboard.press("Escape")
    preview.wait_for(state="detached", timeout=WAIT_MS)
    assert proof_chip.evaluate("element => document.activeElement === element") is True

    history = page.get_by_label("Sprint Item conversation")
    history.select_option(past_id)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    assert page.get_by_text("Past supervisor marker", exact=True).count() == 0
    page.locator("[data-conversation-lens-toggle]").click()
    page.get_by_text("Past supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    assert page.locator('[data-conversation-read-only-boundary="true"]').count() == 1
    history.select_option("__current__")
    assert page.locator("[data-conversation-lens-toggle]").inner_text() == "Full"
    page.get_by_text("Current supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    page.locator("[data-conversation-lens-toggle]").click()
    assert page.get_by_text("Current supervisor marker", exact=True).count() == 0

    api.direct_patch(
        server,
        f"/api/items/{item['id']}",
        {"body": "The shared brief changed through the live route."},
    )
    page.get_by_text("The shared brief changed through the live route.", exact=True).wait_for(
        timeout=WAIT_MS
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[data-sprint-item-view]").wait_for(timeout=WAIT_MS)
    assert page.locator("[data-conversation-input]").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= 390") is True
    narrow_geometry = page.evaluate(
        """([rowSelector]) => {
            const row = document.querySelector(rowSelector);
            const title = row.querySelector('.ticket-row-title').getBoundingClientRect();
            return {
                rowWidth: row.getBoundingClientRect().width,
                titleWidth: title.width
            };
        }""",
        [f'[data-sprint-ticket-id="{backlog_ticket["id"]}"]'],
    )
    assert narrow_geometry["titleWidth"] > narrow_geometry["rowWidth"] / 2, narrow_geometry
    assert artifacts.get_by_role("button", name="Show fewer").is_hidden()
    assert artifacts.locator("[data-artifact-chip]").nth(6).is_visible()
    assert artifacts.locator(".artifact-strip-items").evaluate(
        "element => element.scrollWidth >= element.clientWidth"
    ) is True

    # The same component also renders from the Workspace route, where no Sprint screen
    # surrounds it. That route is where the pane's own rules were dead, so a phone is
    # proven here as well: the rules have to travel with the pane and not with a route.
    column = (
        "([column, doc]) => { const el = document.querySelector(column);"
        " const style = getComputedStyle(el);"
        " return [Math.round(el.getBoundingClientRect().width)"
        " === document.querySelector(doc).clientWidth,"
        " style.paddingLeft, style.paddingRight]; }"
    )
    page.goto(f"{server.base}/#/workspace/item/{item['id']}")
    page.locator(f'[data-sprint-item-view="{item["id"]}"]').wait_for(timeout=WAIT_MS)
    assert page.locator("[data-conversation-input]").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= 390") is True
    assert (
        page.evaluate(
            "() => { const pane = document.querySelector('[data-sprint-item-view]');"
            " return pane.scrollWidth <= pane.clientWidth; }"
        )
        is True
    )
    item_column = page.evaluate(column, [".sprint-item-column", ".sprint-item-doc"])
    assert item_column == [True, "16px", "16px"], item_column
    # The conversation column is what proves the pane's own rules are alive on this
    # route: its inset is 48px unless a query on this pane brings it down. The column
    # above would read 16px from an unconditional rule even with every query dead.
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.conversation-column')).paddingLeft"
        )
        == "16px"
    )

    # An Item and a Ticket open one after the other in this same slot, so they read
    # against one edge only if a single measure serves both. The Ticket answers for the
    # Item, at a phone width and again past the width where the column takes the wider
    # inset — one sample cannot see a rule that only applies beyond it.
    page.goto(f"{server.base}/#/workspace/{today_ticket['id']}")
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    assert item_column == page.evaluate(column, [".ticket-col", ".ticket-doc"])

    page.set_viewport_size({"width": 2200, "height": 1200})
    page.locator(".ticket-col").wait_for(timeout=WAIT_MS)
    ticket_wide = page.evaluate(column, [".ticket-col", ".ticket-doc"])
    assert ticket_wide == [False, "48px", "48px"], ticket_wide
    page.goto(f"{server.base}/#/workspace/item/{item['id']}")
    page.locator(f'[data-sprint-item-view="{item["id"]}"]').wait_for(timeout=WAIT_MS)
    assert ticket_wide == page.evaluate(column, [".sprint-item-column", ".sprint-item-doc"])

    # A window this wide keeps the rail, so the pane beside it is narrow while the window
    # is not. Both panes must read their own width here. This is the only band that tells
    # a pane query from a window query, so it is the only place a return to a window query
    # shows up as the step it is.
    inset = "() => getComputedStyle(document.querySelector('%s')).paddingLeft"
    page.set_viewport_size({"width": 1000, "height": 900})
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    assert page.evaluate(inset % ".conversation-column") == "24px"
    page.goto(f"{server.base}/#/workspace/{today_ticket['id']}")
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    assert page.evaluate(inset % ".conversation-column") == "24px"
