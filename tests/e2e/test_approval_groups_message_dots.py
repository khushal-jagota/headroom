"""Protect the real query, transcript delivery and durable owner-read boundary.

Frontend tests own the attention matrix. This live server proof is needed because a
rendered transcript must advance the same SQLite read position that board/Chief queries
consume; mocked fetch tests cannot establish that connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import httpx
from playwright.sync_api import BrowserContext, Page, Route, expect
from playwright.sync_api import Error as PlaywrightError
from tests.e2e.approval_message_diagnostics import ApprovalMessageDiagnostics
from tests.e2e.harness import (
    WAIT_MS,
    ApiHelper,
    ServerHandle,
    capture_success,
)
from tests.support.connected_rows import _bind
from tests.support.connected_rows import _conversation as _conversation
from tests.support.connected_rows import _message as _message
from tests.support.connected_rows import _post as _post
from tests.support.server_fixtures import rail_server as rail_server

from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind

EVIDENCE = Path("data/verification/approval-message-dots")


def _read_position(server: ServerHandle, conversation: str) -> int:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute(
            "SELECT owner_read_through_sequence FROM conversations WHERE conversation_id=?",
            (conversation,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_approval_membership_and_each_workers_durable_message_dot(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    project = _post(server, "/api/projects", {"name": "Message proof"})
    item = _post(
        server,
        "/api/items",
        {"title": "Approval and message proof", "project_id": project["id"]},
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
                "brief": "The brief is agreed.",
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
            "brief": "Review is ready.",
        },
    )
    _bind(server, "feedback", feedback["id"])
    ticket = tickets[0]
    for name in ("dot-ticket", "dot-supervisor", "dot-chief"):
        _conversation(server, name)
    _bind(server, "ticket", ticket["id"], "dot-ticket")
    _bind(server, "agent", item["manager"]["agent_key"], "dot-supervisor")
    _bind(server, "agent", "chief_of_staff", "dot-chief")
    _message(
        server, "dot-ticket", Principal(PrincipalKind.ticket, ticket["id"]), "Child direct message"
    )

    context = context_factory()
    diagnostics = ApprovalMessageDiagnostics(context, server, EVIDENCE)
    transcript_pattern = "**/api/conversation/conversations/dot-ticket/view/tail?*transcript=true*"
    pending_events: list[Route] = []
    pending_workspace: list[tuple[Route, str]] = []

    def delay_transcript(route: Route) -> None:
        pending_events.append(route)
        diagnostics.record("transcript-held", url=route.request.url)
        route.request.frame.page.evaluate("() => { window.__proofTranscriptHeld = true; }")

    try:
        diagnostics.record("transcript-route-install-start")
        context.route(transcript_pattern, delay_transcript)
        diagnostics.record("transcript-route-install-return")
        page = open_page(context, server, "#/workspace?view=items", '[data-screen="workspace"]')
        left = page.locator(f'[data-manager="{item["id"]}"]')
        owner_row = left.locator(f'[data-ticket-id="{ticket["id"]}"]')
        expect(owner_row.locator('[data-conversation-signal="message"]')).to_have_count(
            1, timeout=WAIT_MS
        )
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

        workspace_key = quote(f"/api/managers/{item['id']}/workspace-content", safe="")
        workspace_pattern = f"**/api/resource-body?key={workspace_key}"
        diagnostics.record("workspace-route-install-start")
        context.route(workspace_pattern, delay_workspace)
        diagnostics.record("workspace-route-install-return")
        left.locator(".item-head").click(timeout=WAIT_MS)
        page.wait_for_function("() => window.__proofWorkspaceHeld === true", timeout=WAIT_MS)
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        for route, body in pending_workspace:
            diagnostics.record("workspace-release", url=route.request.url, body=body)
            route.fulfill(status=200, content_type="application/json", body=body)
        pending_workspace.clear()
        diagnostics.record("workspace-route-remove-start")
        context.unroute(workspace_pattern, delay_workspace)
        diagnostics.record("workspace-route-remove-return")
        right = page.locator(f'[data-manager-workspace="{item["id"]}"]')
        expect(right.locator('[data-workspace-group="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        expect(right.locator('[data-workspace-group="status_awaiting_approval"]')).to_contain_text(
            tickets[1]["title"]
        )
        expect(left.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(
            ticket["title"]
        )
        assert right.locator(".manager-workspace-title-mark").count() == 0
        # Unfinished groups arrive open. The two approval holders remain distinct.
        for key in ("awaiting_approval", "status_awaiting_approval"):
            group = right.locator(f'[data-workspace-group="{key}"]')
            expect(group).to_have_attribute("open", "")
            expect(group.locator("[data-group-count]")).to_have_text("1")
            expect(group.locator("[data-group-count]")).to_be_visible()
        expect(right.get_by_text(tickets[1]["title"], exact=True)).to_be_visible(timeout=WAIT_MS)
        capture_success(page, str(EVIDENCE / "approvals-child-message.png"))

        owner_row.click(timeout=WAIT_MS)
        page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
        page.wait_for_function(
            "() => document.querySelector('[data-conversation-state=opened]')", timeout=WAIT_MS
        )
        # Opened is local UI state; metadata and reader bootstrap can still be pending.
        # Establish that the interceptor holds delivery before testing the read boundary.
        page.wait_for_function("() => window.__proofTranscriptHeld === true", timeout=WAIT_MS)
        # The snapshot can name an unread latest sequence, but undelivered rows cannot be read.
        assert pending_events
        assert _read_position(server, "dot-ticket") == 0
        for route in pending_events:
            diagnostics.record("transcript-release", url=route.request.url)
            route.continue_()
        pending_events.clear()
        diagnostics.record("transcript-route-remove-start")
        context.unroute(transcript_pattern, delay_transcript)
        diagnostics.record("transcript-route-remove-return")
        page.locator('[data-conversation-lens-choice="full"]').click(timeout=WAIT_MS)
        expect(page.get_by_text("Child direct message", exact=True)).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_be_visible(timeout=WAIT_MS)
        expect(owner_row.locator("[data-conversation-signal]")).to_have_count(0, timeout=WAIT_MS)
        assert _read_position(server, "dot-ticket") == 1

        # The read is durable across refresh. A stale acknowledgement cannot eat a newer message.
        page.goto(server.base + "/#/workspace?view=items")
        page.reload()
        reloaded_owner_row = page.locator(f'[data-ticket-id="{ticket["id"]}"]')
        expect(reloaded_owner_row).to_be_visible(timeout=WAIT_MS)
        expect(reloaded_owner_row.locator("[data-conversation-signal]")).to_have_count(
            0, timeout=WAIT_MS
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
        expect(
            page.locator(f'[data-ticket-id="{ticket["id"]}"]').locator(
                '[data-conversation-signal="message"]'
            )
        ).to_have_count(1, timeout=WAIT_MS)
        assert _read_position(server, "dot-ticket") == 1
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        assert (
            page.locator(f'[data-manager="{item["id"]}"] .item-head .stage-mark').count() == 0
        )

        _post(
            server, "/api/conversation/conversations/dot-ticket/owner-read", {"through_sequence": 2}
        )
        _message(
            server,
            "dot-supervisor",
            Principal(PrincipalKind.manager, item["id"]),
            "Supervisor direct message",
        )
        page.reload()
        expect(left.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_be_visible(timeout=WAIT_MS)
        expect(owner_row.locator("[data-conversation-signal]")).to_have_count(0, timeout=WAIT_MS)
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        left.locator(".item-head").click(timeout=WAIT_MS)
        expect(right.locator(".manager-workspace-title-mark")).to_be_visible(timeout=WAIT_MS)
        expect(left.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
        expect(owner_row).to_be_visible(timeout=WAIT_MS)
        expect(owner_row.locator("[data-conversation-signal]")).to_have_count(0, timeout=WAIT_MS)
        assert page.locator("[data-chief-destination] .stage-mark").count() == 0
        # Selecting the Item keeps the conversation at rest and does not claim a read.
        expect(page.locator('[data-conversation-state="rest"]')).to_be_visible(timeout=WAIT_MS)
        assert _read_position(server, "dot-supervisor") == 0
        capture_success(page, str(EVIDENCE / "supervisor-own-message-title-and-rail.png"))
        page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
        expect(right.locator(".manager-workspace-title-mark")).to_have_count(0, timeout=WAIT_MS)
        expect(left.locator(".item-head .stage-mark")).to_have_count(0, timeout=WAIT_MS)
        assert _read_position(server, "dot-supervisor") == 1
        page.goto(server.base + "/#/workspace?view=items")
        _message(server, "dot-chief", CHIEF_PRINCIPAL, "Chief direct message")
        page.reload()
        expect(page.locator("[data-chief-destination] .stage-mark")).to_be_visible(timeout=WAIT_MS)
        assert left.locator(".item-head .stage-mark").count() == 0
        expect(owner_row).to_be_visible(timeout=WAIT_MS)
        expect(owner_row.locator("[data-conversation-signal]")).to_have_count(0, timeout=WAIT_MS)
        capture_success(page, str(EVIDENCE / "chief-own-message-only.png"))
        page.locator("[data-chief-destination]").click(timeout=WAIT_MS)
        expect(page.locator('[data-conversation-state="rest"]')).to_be_visible(timeout=WAIT_MS)
        assert _read_position(server, "dot-chief") == 0
        page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
        page.locator('[data-conversation-lens-choice="full"]').click(timeout=WAIT_MS)
        expect(
            page.locator("[data-conversation-transcript]").get_by_text(
                "Chief direct message", exact=True
            )
        ).to_be_visible(timeout=WAIT_MS)
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
        diagnostics.persist_and_close(context)
        # Failed assertions must not strand intercepted requests during context teardown.
        for route in pending_events:
            try:
                route.continue_()
            except PlaywrightError:
                pass  # Already handled or the test's page closed.
        for route, body in pending_workspace:
            try:
                route.fulfill(status=200, content_type="application/json", body=body)
            except PlaywrightError:
                pass  # Already handled or the test's page closed.
        context.unroute_all(behavior="ignoreErrors")
