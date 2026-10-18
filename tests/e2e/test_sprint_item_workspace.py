"""The orchestrator route composes its Workers, artifacts, transcript and live updates."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, Route, expect
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


def test_workspace_rail_and_item_detail_share_one_live_snapshot(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    project = _post(
        server,
        "/api/projects",
        {"name": "Mirror project", "summary": "", "priority": "P1"},
    )
    item = _post(
        server,
        "/api/items",
        {"title": "Mirror Item", "project_id": project["id"], "priority": "P1"},
    )
    ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Mirror Ticket",
            "worker_type": "coding",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    quiet = {
        "awaiting_reply": False,
        "awaiting_answer": False,
        "awaiting_approval": False,
        "awaiting_agent_approval": False,
        "assigned": False,
        "agent_state": "idle",
        "conversation_signal": {"working": False, "message_for_owner": False},
    }
    mutable = {
        "awaiting_reply": False,
        "assigned": True,
        "agent_state": "working",
        "conversation_signal": {"working": True, "message_for_owner": False},
    }

    def attention() -> JsonObject:
        return {**quiet, **mutable}

    def workspace_ticket() -> JsonObject:
        return {
            "id": ticket["id"],
            "title": "Mirror Ticket",
            "stage": "needs_brief",
            "priority": "P1",
            "activity_at": 20,
            "ticket_status": "empty",
            "waiting_to_closeout": False,
            "has_pending_proposal": False,
            "gating_field": "brief",
            "blocked": False,
            "worker_type": "coding",
            "day_ids": [],
            "sprint_id": None,
            "sprint_name": None,
            "conversation_id": "mirror-child-conversation",
            **attention(),
        }

    def workspace_payload() -> JsonObject:
        return {
            "id": item["id"],
            "title": "Mirror Item",
            "body": "One source for both panes.",
            "priority": "P1",
            "project_id": project["id"],
            "project": "Mirror project",
            "created_at": 10,
            "updated_at": 10,
            "committed_sprints": [],
            "supervisor": {
                "agent_key": item["supervisor"]["agent_key"],
                "conversation_id": None,
                "launch_configuration": item["supervisor"]["launch_configuration"],
            },
            "planning_day_id": None,
            "today_ticket_ids": [],
            "tickets": [workspace_ticket()],
            "ticket_rollup": attention(),
            "artifacts": [],
            "conversation_history": [],
            **quiet,
        }

    def board_payload() -> JsonObject:
        row = {
            **workspace_ticket(),
            "deadline": None,
            "project_id": project["id"],
            "project": "Mirror project",
            "group_project_id": project["id"],
            "group_project": "Mirror project",
            "stage_label": "Brief",
            "gating_field_label": "Brief",
            "is_done": False,
            "conversation_id": "mirror-child-conversation",
            "employee_backend": "codex",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
            "sprint_item_id": item["id"],
            "sprint_item_title": "Mirror Item",
            "sprint_item_priority": "P1",
        }
        summary = {
            "id": item["id"],
            "created_at": 10,
            "title": "Mirror Item",
            "priority": "P1",
            "tickets": [workspace_ticket()],
            "conversation_id": None,
            "ticket_rollup": attention(),
            **quiet,
        }
        return {"columns": [{"stage": "needs_brief", "cards": [row]}], "sprint_items": [summary]}

    context = context_factory()

    def mirror_reads(route: Route) -> None:
        request_url = route.request.url
        if request_url.split("?", 1)[0].endswith("/api/board"):
            route.fulfill(json=board_payload())
            return
        if request_url.split("?", 1)[0].endswith(f"/api/items/{item['id']}/workspace"):
            route.fulfill(json=workspace_payload())
            return
        route.continue_()

    context.route("**/api/**", mirror_reads)
    page = open_page(
        context,
        server,
        f"#/workspace/item/{item['id']}",
        f'[data-sprint-item-workspace="{item["id"]}"]',
    )
    left_item = page.locator(f'[data-sprint-item="{item["id"]}"]')
    right_item = page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
    left_ticket = left_item.locator(f'[data-ticket-id="{ticket["id"]}"]')
    right_ticket = right_item.locator(f'[data-sprint-ticket-id="{ticket["id"]}"]')

    assigned_group = right_item.locator('[data-workspace-group="assigned"]')
    expect(assigned_group).to_have_attribute("open", "")
    # Manual closure and reopening remain available with the new default.
    assigned_group.locator(":scope > summary").click()
    expect(assigned_group).not_to_have_attribute("open", "")
    assigned_group.locator(":scope > summary").click()
    for row in (left_ticket, right_ticket):
        row.wait_for(timeout=WAIT_MS)
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(1)
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(0)
    assert left_item.locator('[data-bucket-key="assigned"]').count() == 1
    assert right_item.locator('[data-workspace-group="assigned"]').count() == 1
    # Child work never supplies a dot on the supervisor conversation.
    assert left_item.locator(".item-head .stage-mark").count() == 0
    assert right_item.locator(".sprint-workspace-title-mark").count() == 0

    # Keep a manual closure when live refresh moves this Worker to another group,
    # then back. A full reload starts with the approved open default again.
    assigned_group.locator(":scope > summary").click()
    expect(assigned_group).not_to_have_attribute("open", "")
    before = int(page.evaluate("() => window.__plannerDebug.flushes"))
    mutable.update(
        awaiting_answer=True,
        awaiting_reply=True,
        agent_state="working",
        conversation_signal={"working": True, "message_for_owner": True},
    )
    api.direct_patch(server, f"/api/tickets/{ticket['id']}", {"title": "Signal mirror refresh"})
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=before,
        timeout=WAIT_MS,
    )
    answer_group = right_item.locator('[data-workspace-group="awaiting_answer"]')
    expect(answer_group).to_have_attribute("open", "")
    expect(answer_group.locator("[data-group-count]")).to_have_text("1")
    expect(answer_group.locator("[data-group-count]")).to_be_visible()
    for row in (left_ticket, right_ticket):
        row.wait_for(timeout=WAIT_MS)
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(
            1, timeout=WAIT_MS
        )
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
    assert left_item.locator(".item-head .stage-mark").count() == 0
    assert right_item.locator(".sprint-workspace-title-mark").count() == 0
    assert left_item.locator('[data-bucket-key="awaiting_answer"]').count() == 1
    assert right_item.locator('[data-workspace-group="awaiting_answer"]').count() == 1

    before = int(page.evaluate("() => window.__plannerDebug.flushes"))
    mutable.update(
        awaiting_answer=False,
        agent_state="idle",
        conversation_signal={"working": False, "message_for_owner": True},
    )
    api.direct_patch(
        server,
        f"/api/tickets/{ticket['id']}",
        {"title": "Signal second mirror refresh"},
    )
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=before,
        timeout=WAIT_MS,
    )
    expect(assigned_group).not_to_have_attribute("open", "")
    assigned_group.locator(":scope > summary").click()
    expect(assigned_group).to_have_attribute("open", "")
    for row in (left_ticket, right_ticket):
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(
            1, timeout=WAIT_MS
        )
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)

    page.reload()
    expect(assigned_group).to_have_attribute("open", "")
    right_ticket.wait_for(timeout=WAIT_MS)
    for row in (left_ticket, right_ticket):
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
    # A canonical unread message masks the spinner. A read clears only the message
    # fact, so the same ongoing work appears in both mounted projections.
    before = int(page.evaluate("() => window.__plannerDebug.flushes"))
    mutable.update(
        agent_state="working",
        conversation_signal={"working": True, "message_for_owner": True},
    )
    api.direct_patch(server, f"/api/tickets/{ticket['id']}", {"title": "Message masks work"})
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=before,
        timeout=WAIT_MS,
    )
    for row in (left_ticket, right_ticket):
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
    before = int(page.evaluate("() => window.__plannerDebug.flushes"))
    mutable.update(
        awaiting_reply=False,
        conversation_signal={"working": True, "message_for_owner": False},
    )
    api.direct_patch(server, f"/api/tickets/{ticket['id']}", {"title": "Read reveals ongoing work"})
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=before,
        timeout=WAIT_MS,
    )
    for row in (left_ticket, right_ticket):
        expect(row.locator('[data-conversation-signal="message"]')).to_have_count(0)
        expect(row.locator('[data-conversation-signal="working"]')).to_have_count(1)
    if evidence_path := os.environ.get("PANELS_E2E_EVIDENCE_PATH"):
        page.screenshot(path=evidence_path, full_page=True)


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
    ordinary_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Ordinary Worker in this orchestrator",
            "worker_type": "coding",
            "brief": "Start.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    approval_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Review the pending result",
            "worker_type": "coding",
            "brief": "Start.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    agent_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Keep the complete Worker title visible",
            "worker_type": "coding",
            "brief": "Start.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    api.direct_post(
        server,
        f"/api/tickets/{approval_ticket['id']}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    proposed = httpx.post(
        f"{server.base}/api/tickets/{approval_ticket['id']}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(approval_ticket["id"])},
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
    # A linked site and a folder of write-ups: the two shapes the strip has to fold. The
    # stylesheet is what the index loads, so it is not a thing to read on its own.
    for folded_path, content in (
        ("site/index.html", "<h1>Site</h1>"),
        ("site/style.css", "body { color: red; }"),
        ("notes/one.md", "# One"),
        ("notes/two.md", "# Two"),
    ):
        folded = httpx.put(
            f"{server.base}/files/sprint-items/{item['id']}/artifacts/{folded_path}",
            headers={
                "X-Plan-Actor": "sprint_item_supervisor",
                "X-Plan-Sprint-Item-ID": str(item["id"]),
            },
            json={"content": content},
            timeout=10.0,
        )
        assert folded.status_code < 300, folded.text
    api.direct_patch(
        server,
        f"/api/items/{item['id']}",
        {"body": f"[Open proof](/files/sprint-items/{item['id']}/artifacts/proof.md)"},
    )

    past_id = "conv_workspace_past"
    active_id = "conv_workspace_active"
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET worker_step_claim = 'out', pending_proposal = NULL WHERE id = ?",
            (agent_ticket["id"],),
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
        f"#/workspace/item/{item['id']}",
        f'[data-sprint-item-view="{item["id"]}"]',
    )
    pane = page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
    expect(pane.locator('[data-workspace-section="today"]')).to_have_count(0)
    expect(pane.locator('[data-workspace-section="remaining"]')).to_have_count(0)
    expect(pane.locator('[data-workspace-group="awaiting_reply"]')).to_have_count(0)
    approval_group = pane.locator('[data-workspace-group="awaiting_approval"]')
    expect(approval_group).to_have_attribute("open", "")
    page.locator(f'[data-sprint-ticket-id="{ordinary_ticket["id"]}"]').wait_for(timeout=WAIT_MS)
    approval_row = page.locator(f'[data-sprint-ticket-id="{approval_ticket["id"]}"]')
    expect(approval_row).to_be_visible()
    expect(approval_row.locator(".stage-mark")).to_have_count(0)
    # Both the initial brief and the explicit success proposal await owner approval.
    expect(approval_group.locator("[data-group-count]")).to_have_text("2")
    expect(approval_group.locator("[data-group-count]")).to_be_visible()
    agent_group = pane.locator('[data-workspace-group="agent"]')
    expect(agent_group).to_have_attribute("open", "")
    agent_row = page.locator(f'[data-sprint-ticket-id="{agent_ticket["id"]}"]')
    agent_title = agent_row.get_by_text("Keep the complete Worker title visible", exact=True)
    expect(agent_title).to_be_visible()
    expect(agent_row.locator("[data-ticket-backlog]")).to_have_count(0)
    # Group headings carry ownership. Only direct unread messages supply a dot.
    expect(agent_row.locator(".stage-mark")).to_have_count(0)
    expect(agent_row).to_have_attribute(
        "href", f"#/workspace/item/{item['id']}/{agent_ticket['id']}"
    )
    assert (
        agent_row.locator("xpath=ancestor::details[1]").get_attribute("data-workspace-group")
        == "agent"
    )
    title_box = agent_title.bounding_box()
    assert title_box is not None and title_box["width"] > 200
    artifact_band = pane.locator("[data-artifacts-band]")
    expect(artifact_band).not_to_have_attribute("open", "")
    artifact_band.locator(":scope > summary").focus()
    page.keyboard.press("Enter")
    artifacts = artifact_band.locator("[data-artifact-strip]")
    expect(artifacts).to_be_visible()
    # Eleven files, nine things to open: the site is its index, and the stylesheet beside
    # that index never appears on its own. All artifact rows use the closed page band.
    assert artifacts.locator("[data-artifact-chip]").count() == 8
    assert artifacts.locator('[data-artifact-chip="artifacts/site/index.html"]').count() == 1
    assert artifacts.locator('[data-artifact-chip="artifacts/site/style.css"]').count() == 0
    assert artifacts.locator("[data-artifact-chip]").nth(6).is_visible()
    assert artifacts.get_by_role("button", name="+4 more").count() == 0
    # A folder with no index opens where it stands rather than sending the reader away.
    notes = artifacts.locator('[data-artifact-folder="notes"]')
    assert notes.get_attribute("aria-expanded") == "false"
    assert artifacts.locator('[data-artifact-chip="artifacts/notes/one.md"]').count() == 0
    notes.click()
    artifacts.locator('[data-artifact-chip="artifacts/notes/one.md"]').wait_for(timeout=WAIT_MS)
    assert notes.get_attribute("aria-expanded") == "true"

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

    # The picker lives in the card's own options menu, which is in the head. No row of
    # chrome sits above the card, so opening the card is what reaches the picker.
    assert page.locator("[data-ticket-conversation-history]").count() == 0
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    options = page.get_by_role("button", name="Conversation options")
    options.click()
    history = page.get_by_label("Orchestrator conversation")
    history.select_option(past_id)
    assert page.get_by_text("Past supervisor marker", exact=True).count() == 0
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.get_by_text("Past supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    assert page.locator('[data-conversation-read-only-boundary="true"]').count() == 1
    assert page.locator("[data-conversation-new-arm]").count() == 0
    options.click()
    # One Tab from the options button reaches the picker, so the way back is on the
    # keyboard as well as under the pointer.
    options.press("Tab")
    picker = page.locator("[data-conversation-history] select")
    assert picker.evaluate("element => document.activeElement === element") is True
    picker.select_option("__current__")
    assert (
        page.locator('[data-conversation-lens-choice="full"]').get_attribute("aria-pressed")
        == "true"
    )
    page.get_by_text("Current supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    page.locator('[data-conversation-lens-choice="focus"]').click()
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
        [f'[data-sprint-ticket-id="{agent_ticket["id"]}"]'],
    )
    assert narrow_geometry["titleWidth"] > narrow_geometry["rowWidth"] / 2, narrow_geometry
    assert artifacts.locator("[data-artifact-chip]").nth(6).is_visible()
    assert (
        artifacts.locator(".artifact-strip-items").evaluate(
            "element => element.scrollWidth >= element.clientWidth"
        )
        is True
    )

    # At phone and wide widths, each approved document stays within its own pane.
    # Worker and orchestrator interiors deliberately have different reading measures.
    def assert_pane_fits() -> None:
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("""() => {
            const pane = document.querySelector('.board-workspace-right');
            return pane.scrollWidth <= pane.clientWidth;
        }""")
        assert page.locator("[data-conversation-input]").is_visible()

    assert_pane_fits()
    page.goto(f"{server.base}/#/workspace/{ordinary_ticket['id']}")
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    assert_pane_fits()
    for width in (2200, 1000):
        page.set_viewport_size({"width": width, "height": 1100})
        assert_pane_fits()
        page.goto(f"{server.base}/#/workspace/item/{item['id']}")
        page.locator(f'[data-sprint-item-view="{item["id"]}"]').wait_for(timeout=WAIT_MS)
        assert_pane_fits()
        page.goto(f"{server.base}/#/workspace/{ordinary_ticket['id']}")
        page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    # A window this wide keeps the rail, so the pane beside it is narrow while the window
    # is not. Both panes must read their own width here. This is the only band that tells
    # a pane query from a window query, so it is the only place a return to a window query
    # shows up as the step it is.
    inset = "() => getComputedStyle(document.querySelector('%s')).paddingLeft"
    page.set_viewport_size({"width": 1000, "height": 900})
    page.goto(f"{server.base}/#/workspace/item/{item['id']}")
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    # The approved rail is narrower at rest. Widen it explicitly so the pane,
    # unlike the window, crosses the conversation-host breakpoint.
    grip = page.get_by_role("separator", name="Resize workspace panel")
    expect(grip).to_be_visible()
    grip.focus()
    grip.press("Shift+ArrowRight")
    assert page.evaluate("innerWidth > 720")
    assert page.locator(".board-workspace-right").evaluate("pane => pane.clientWidth <= 720")
    assert page.evaluate(inset % ".conversation-column") == "24px"
    page.goto(f"{server.base}/#/workspace/{ordinary_ticket['id']}")
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    assert page.evaluate(inset % ".conversation-column") == "24px"
