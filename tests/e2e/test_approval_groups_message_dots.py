"""Protect the real query, transcript delivery and durable owner-read boundary.

Frontend tests own the attention matrix. This live server proof is needed because a
rendered transcript must advance the same SQLite read position that board/Chief queries
consume; mocked fetch tests cannot establish that connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, Route, expect
from playwright.sync_api import Error as PlaywrightError
from tests.e2e.approval_message_diagnostics import ApprovalMessageDiagnostics
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_ticket_conversation_shared_behaviors import _append_rows

from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind

EVIDENCE = Path("data/verification/approval-message-dots")


def _post(server: ServerHandle, path: str, body: JsonObject) -> JsonObject:
    response = httpx.post(server.base + path, json=body, timeout=10)
    assert response.is_success, response.text
    value: JsonObject = response.json()
    return value


def _conversation(server: ServerHandle, name: str) -> None:
    _post(
        server,
        "/api/conversation/conversations",
        {"conversation_id": name, "model": "e2e-model", "backend_key": "codex"},
    )


def _message(server: ServerHandle, conversation: str, sender: Principal, text: str) -> None:
    _append_rows(
        server,
        conversation,
        MessageToOwnerEventPayload(
            content=text_message_content(text),
            sender=sender,
            recipient=OWNER_PRINCIPAL,
            sender_label="Proof Worker",
        ),
    )


def _read_position(server: ServerHandle, conversation: str) -> int:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute(
            "SELECT owner_read_through_sequence FROM conversations WHERE conversation_id=?",
            (conversation,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_approval_membership_and_each_workers_durable_message_dot(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    project = _post(server, "/api/projects", {"name": "Message proof", "priority": "P1"})
    item = _post(
        server,
        "/api/items",
        {"title": "Approval and message proof", "priority": "P1", "project_id": project["id"]},
    )
    tickets = []
    for title, holder in (
        ("Owner approval without Day membership", {"kind": "owner", "id": "owner"}),
        ("Supervisor approval without Day membership", {"kind": "sprint_item", "id": item["id"]}),
    ):
        ticket = _post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "orchestrator": {"kind": "sprint_item", "id": item["id"]},
                "kickoff_note": "The brief is agreed.",
            },
        )
        api.direct_post(
            server,
            f"/api/tickets/{ticket['id']}/accept/brief",
            {
                "next_ceiling": "needs_success_condition",
                "next_holder": holder,
            },
        )
        response = httpx.post(
            server.base + f"/api/tickets/{ticket['id']}/propose",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
            json={"body": "Consistent group membership.", "recap": "Ready."},
            timeout=10,
        )
        assert response.is_success, response.text
        tickets.append(ticket)
    # Active orchestrators remain represented without Day membership.
    # The user-owned Feedback assignment is not a parked approval.
    feedback = _post(
        server,
        "/api/tickets",
        {
            "title": "User-owned Feedback",
            "worker_type": "initiative_review",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
            "kickoff_note": "Review is ready.",
        },
    )
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage='needs_feedback',pending_proposal=NULL WHERE id=?",
            (feedback["id"],),
        )
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM day_tickets").fetchone()[0] == 0
    ticket = tickets[0]
    for name in ("dot-ticket", "dot-supervisor", "dot-chief"):
        _conversation(server, name)
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?", ("dot-ticket", ticket["id"])
        )
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id,ticket_id) VALUES (?,?)",
            ("dot-ticket", ticket["id"]),
        )
        conn.execute(
            "UPDATE agents SET conversation_id=? WHERE agent_key=?",
            ("dot-supervisor", item["supervisor"]["agent_key"]),
        )
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            ("dot-chief",),
        )
    _message(
        server, "dot-ticket", Principal(PrincipalKind.ticket, ticket["id"]), "Child direct message"
    )

    context = context_factory()
    diagnostics = ApprovalMessageDiagnostics(context, server, EVIDENCE)
    pending_events: list[tuple[Route, str]] = []
    pending_workspace: list[tuple[Route, str]] = []

    def delay_transcript(route: Route) -> None:
        response = route.fetch()
        body = response.text()
        pending_events.append((route, body))
        diagnostics.record("transcript-held", url=route.request.url, body=body)

    try:
        context.route("**/api/conversation/conversations/dot-ticket/events?*", delay_transcript)
        page = open_page(context, server, "#/workspace?view=items", '[data-screen="workspace"]')
        left = page.locator(f'[data-sprint-item="{item["id"]}"]')
        owner_row = left.locator(f'[data-ticket-id="{ticket["id"]}"]')
        expect(owner_row).to_have_attribute("data-workspace-mark", "reply", timeout=WAIT_MS)
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).not_to_contain_text(
            tickets[1]["title"]
        )
        expect(left.locator('[data-bucket-key="assigned"]')).to_contain_text(feedback["title"])
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).not_to_contain_text(
            feedback["title"]
        )
        assert left.locator(".item-head .stage-mark").count() == 0
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0

        def delay_workspace(route: Route) -> None:
            response = route.fetch()
            body = response.text()
            pending_workspace.append((route, body))
            diagnostics.record("workspace-held", url=route.request.url, body=body)
            page.evaluate("() => { window.__proofWorkspaceHeld = true; }")

        workspace_pattern = f"**/api/items/{item['id']}/workspace"
        context.route(workspace_pattern, delay_workspace)
        left.locator(".item-head").click(timeout=WAIT_MS)
        page.wait_for_function("() => window.__proofWorkspaceHeld === true", timeout=WAIT_MS)
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        for route, body in pending_workspace:
            diagnostics.record("workspace-release", url=route.request.url, body=body)
            route.fulfill(status=200, content_type="application/json", body=body)
        pending_workspace.clear()
        context.unroute(workspace_pattern, delay_workspace)
        right = page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
        expect(right.locator('[data-workspace-group="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        expect(right.locator('[data-workspace-group="status_awaiting_approval"]')).to_contain_text(
            tickets[1]["title"]
        )
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        assert right.locator(".sprint-workspace-title-mark").count() == 0
        # Unfinished groups arrive open. The two approval holders remain distinct.
        for key in ("awaiting_approval", "status_awaiting_approval"):
            group = right.locator(f'[data-workspace-group="{key}"]')
            expect(group).to_have_attribute("open", "")
            expect(group.locator("[data-group-count]")).to_have_text("1")
            expect(group.locator("[data-group-count]")).to_be_visible()
        expect(right.get_by_text(tickets[1]["title"], exact=True)).to_be_visible(timeout=WAIT_MS)
        page.screenshot(path=str(EVIDENCE / "approvals-child-message.png"), full_page=True)

        owner_row.click(timeout=WAIT_MS)
        page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
        page.wait_for_function(
            "() => document.querySelector('[data-conversation-state=peeked]')", timeout=WAIT_MS
        )
        # The snapshot can name an unread latest sequence, but undelivered rows cannot be read.
        assert pending_events
        assert _read_position(server, "dot-ticket") == 0
        for route, body in pending_events:
            diagnostics.record("transcript-release", url=route.request.url, body=body)
            route.fulfill(status=200, content_type="application/json", body=body)
        pending_events.clear()
        context.unroute("**/api/conversation/conversations/dot-ticket/events?*", delay_transcript)
        page.locator('[data-conversation-lens-choice="full"]').click(timeout=WAIT_MS)
        expect(page.get_by_text("Child direct message", exact=True)).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_have_attribute("data-workspace-mark", "none", timeout=WAIT_MS)
        assert _read_position(server, "dot-ticket") == 1

        # The read is durable across refresh. A stale acknowledgement cannot eat a newer message.
        page.goto(server.base + "/#/workspace?view=items")
        page.reload()
        expect(page.locator(f'[data-ticket-id="{ticket["id"]}"]')).to_have_attribute(
            "data-workspace-mark", "none", timeout=WAIT_MS
        )
        _message(
            server,
            "dot-ticket",
            Principal(PrincipalKind.ticket, ticket["id"]),
            "New child direct message",
        )
        _post(
            server, "/api/conversation/conversations/dot-ticket/owner-read", {"through_sequence": 1}
        )
        page.reload()
        expect(page.locator(f'[data-ticket-id="{ticket["id"]}"]')).to_have_attribute(
            "data-workspace-mark", "reply", timeout=WAIT_MS
        )
        assert _read_position(server, "dot-ticket") == 1
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        assert (
            page.locator(f'[data-sprint-item="{item["id"]}"] .item-head .stage-mark').count() == 0
        )

        _post(
            server, "/api/conversation/conversations/dot-ticket/owner-read", {"through_sequence": 2}
        )
        _message(
            server,
            "dot-supervisor",
            Principal(PrincipalKind.sprint_item, item["id"]),
            "Supervisor direct message",
        )
        page.reload()
        expect(left.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_have_attribute("data-workspace-mark", "none", timeout=WAIT_MS)
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        left.locator(".item-head").click(timeout=WAIT_MS)
        expect(right.locator(".sprint-workspace-title-mark")).to_be_visible(timeout=WAIT_MS)
        expect(left.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_have_attribute("data-workspace-mark", "none", timeout=WAIT_MS)
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        # Selecting the Item keeps the conversation at rest and does not claim a read.
        expect(page.locator('[data-conversation-state="rest"]')).to_be_visible(timeout=WAIT_MS)
        assert _read_position(server, "dot-supervisor") == 0
        page.screenshot(
            path=str(EVIDENCE / "supervisor-own-message-title-and-rail.png"), full_page=True
        )
        page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
        expect(right.locator(".sprint-workspace-title-mark")).to_have_count(0, timeout=WAIT_MS)
        expect(left.locator(".item-head .stage-mark")).to_have_count(0, timeout=WAIT_MS)
        assert _read_position(server, "dot-supervisor") == 1
        page.goto(server.base + "/#/workspace?view=items")
        _message(server, "dot-chief", CHIEF_PRINCIPAL, "Chief direct message")
        page.reload()
        expect(page.locator("[data-chief-destination] .stage-mark")).to_be_visible(timeout=WAIT_MS)
        assert left.locator(".item-head .stage-mark").count() == 0
        expect(owner_row).to_have_attribute("data-workspace-mark", "none", timeout=WAIT_MS)
        page.screenshot(path=str(EVIDENCE / "chief-own-message-only.png"), full_page=True)
        page.locator("[data-chief-destination]").click(timeout=WAIT_MS)
        expect(page.get_by_text("Chief direct message", exact=True)).to_be_visible(timeout=WAIT_MS)
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(
            0, timeout=WAIT_MS
        )
        assert _read_position(server, "dot-chief") == 1
    except BaseException as error:
        try:
            diagnostics.capture_failure(context, error)
        except Exception as collector_error:
            print(f"Failure evidence collector failed: {collector_error!r}")
        raise
    finally:
        # Failed assertions must not strand intercepted requests during context teardown.
        for route, body in (*pending_events, *pending_workspace):
            try:
                route.fulfill(status=200, content_type="application/json", body=body)
            except PlaywrightError:
                pass  # Already handled or the test's page closed.
        context.unroute_all(behavior="ignoreErrors")
