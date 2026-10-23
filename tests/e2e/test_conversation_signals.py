"""Prove runtime → change stream → mounted shared conversation marks.

The material risk is that commit invalidation precedes settled runtime state,
or a consumer substitutes approval/child facts for its own conversation.
The owner requires one visible indicator: message takes precedence over work;
a read must reveal the spinner while the same actual work continues.
Neither a component test nor an API-only test exercises that complete boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import BrowserContext, Locator, Page, Route, expect
from tests.e2e.harness import (
    REPO_ROOT,
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
    capture_success,
)
from tests.support.connected_rows import _bind
from tests.support.server_fixtures import signal_server as signal_server

EVIDENCE = REPO_ROOT / "data/verification/conversation-signals/message-precedence-revision"


def _post(server: ServerHandle, path: str, body: dict[str, object] | None = None) -> JsonObject:
    response = httpx.post(server.base + path, json=body or {}, timeout=10)
    assert response.is_success, response.text
    result: JsonObject = response.json()
    return result


def _marks(root: Locator, working: bool, message: bool) -> None:
    # Arguments are independent backend facts; presentation has one winner.
    for kind, present in (("working", working and not message), ("message", message)):
        expect(root.locator(f'[data-conversation-signal="{kind}"]')).to_have_count(
            int(present),
            timeout=WAIT_MS,
        )


@pytest.mark.parametrize("layout", ["desktop"])
def test_exact_conversation_signals_cross_runtime_stream_and_surfaces(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    layout: str,
) -> None:
    server = signal_server
    evidence = server.db_path.parent / "evidence"
    evidence.mkdir()
    project = api.direct_post(server, "/api/projects", {"name": "Signal proof"})
    item = api.direct_post(
        server,
        "/api/managers",
        {
            "title": "Signal manager",
            "project_id": project["id"],
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Signal Worker",
            "worker_type": "coding",
            "brief": "Agreed brief.",
            "manager": {"kind": "manager", "id": item["id"]},
        },
    )
    api.direct_post(
        server,
        f"/api/tickets/{ticket['id']}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    proposed = httpx.post(
        server.base + f"/api/tickets/{ticket['id']}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
        json={"body": "Quiet approval remains visible.", "recap": "Browser signal proof."},
        timeout=10,
    )
    assert proposed.is_success, proposed.text
    identities = {
        "proof-worker": {"kind": "ticket", "id": ticket["id"], "label": "Signal Worker"},
        "proof-supervisor": {
            "kind": "manager",
            "id": item["id"],
            "label": "Signal manager",
        },
        "proof-chief": {"kind": "chief", "id": "chief", "label": "Chief of Staff"},
    }
    for conversation in identities:
        _post(server, f"/api/test/signals/{conversation}/start")
        _post(server, f"/api/test/signals/{conversation}/finish")
    _bind(server, "ticket", ticket["id"], "proof-worker")
    _bind(server, "agent", item["manager"]["agent_key"], "proof-supervisor")
    _bind(server, "agent", "chief_of_staff", "proof-chief")
    context = context_factory()
    page = open_page(context, server, "#/workspace?view=items", '[data-screen="workspace"]')
    page.set_viewport_size({"width": 1440 if layout == "desktop" else 560, "height": 1000})
    worker = page.locator(f'.board-workspace-left [data-ticket-id="{ticket["id"]}"]')
    supervisor = page.locator(f'[data-manager="{item["id"]}"] .item-head')
    chief = page.locator("[data-chief-destination]")
    rows = {"proof-worker": worker, "proof-supervisor": supervisor, "proof-chief": chief}
    for row in rows.values():
        expect(row).to_be_visible(timeout=WAIT_MS)
        _marks(row, False, False)
    page.evaluate("""() => {
        window.signalProofEvents = [];
        window.signalProofStream = new EventSource('/api/changes');
        window.signalProofStream.onmessage = event => window.signalProofEvents.push(event.data);
    }""")
    page.wait_for_function("() => window.signalProofStream.readyState === 1", timeout=WAIT_MS)
    observations: list[JsonObject] = []
    selected = rows if layout == "desktop" else {"proof-worker": worker}
    for conversation, row in selected.items():
        popup_signal: Locator | None = None
        if layout == "desktop":
            # Keep the same popup mounted before the actual second turn starts.
            row.hover()
            popup = page.locator('[data-rail-hover-panel][aria-hidden="false"]')
            expect(popup).to_be_visible(timeout=WAIT_MS)
            popup_signal = popup.locator(f'[data-conversation-id="{conversation}"]')
            _marks(popup_signal, False, False)
        before = page.evaluate("window.signalProofEvents.length")
        _post(server, f"/api/test/signals/{conversation}/start")
        if popup_signal is not None:
            _marks(row, True, False)
            _marks(popup_signal, True, False)
        page.wait_for_function(
            "before => window.signalProofEvents.length > before", arg=before, timeout=WAIT_MS
        )
        _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
        _marks(row, True, True)
        if popup_signal is not None:
            _marks(popup_signal, True, True)
        if conversation == "proof-worker":
            _marks(supervisor, False, False)
            _marks(chief, False, False)
        if conversation == "proof-worker" and popup_signal is not None:
            # Worker popup resolves the current board card, not captured props.
            view = api.get(server, f"/api/conversation/conversations/{conversation}")
            _post(
                server,
                f"/api/conversation/conversations/{conversation}/owner-read",
                {
                    "through_sequence": view["latest_sequence"],
                },
            )
            _marks(popup_signal, True, False)
            _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
            _marks(popup_signal, True, True)
        pending_reads: list[Route] = []

        def defer_read(route: Route, *, reads: list[Route] = pending_reads) -> None:
            reads.append(route)

        read_pattern = f"**/api/conversation/conversations/{conversation}/owner-read"
        page.route(read_pattern, defer_read)
        row.click()
        pane = page.locator(".board-workspace-right")
        pane_signal = pane.locator(f'[data-conversation-id="{conversation}"]')
        _marks(pane_signal, True, True)
        page.mouse.move(1400 if layout == "desktop" else 540, 350)
        expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
        if conversation == "proof-worker" and layout == "desktop":
            expect(page.locator("[data-approval-block] h2")).to_contain_text("to approve")
            delivered = _post(
                server,
                f"/api/tickets/{ticket['id']}/conversation/send",
                {
                    "conversation_id": conversation,
                    "sender_label": "Owner",
                    "mode": "steer",
                    "content": [{"piece": "text", "text": "An ordinary owner reply."}],
                },
            )
            assert delivered == {"conversation_id": conversation, "fate": "injected"}
            # An addressed owner send acknowledges its captured record. A new explicit
            # message stays unread until the held browser acknowledgement reaches it.
            _marks(pane_signal, True, False)
            _post(server, f"/api/test/signals/{conversation}/message", identities[conversation])
            _marks(pane_signal, True, True)
        capture_success(page, evidence / f"{conversation}-{layout}-page-message.png")
        rest = pane.locator("[data-conversation-rest-bar]")
        if rest.is_visible():
            with page.expect_request(read_pattern, timeout=WAIT_MS):
                rest.click()
            expect(page.locator('[data-conversation-state="peeked"]')).to_be_visible()
        assert pending_reads, "An eligible open conversation must acknowledge its message"
        for read_route in list(pending_reads):
            observations.append(
                {"conversation": conversation, "browser_read": read_route.request.post_data}
            )
            read_route.continue_()
        page.unroute(read_pattern, defer_read)
        _marks(pane_signal, True, False)
        _marks(row, True, False)
        _post(server, f"/api/test/signals/{conversation}/finish")
        _marks(pane_signal, False, False)
        _marks(row, False, False)
        page.evaluate("location.hash = '#/workspace?view=items'")
        expect(row).to_be_visible(timeout=WAIT_MS)
        _marks(row, False, False)
    events = page.evaluate("window.signalProofEvents")
    assert events and set(events) == {"change"}, events
    (evidence / "observations.json").write_text(
        json.dumps(
            {
                "observations": observations,
                "change_events": events,
                "layout": layout,
                "server": server.base,
                "db_path": str(server.db_path),
                "backend": "scripted adapter; real conversation core; real fixed refresh window",
            },
            indent=2,
        )
    )
    page.evaluate("window.signalProofStream.close()")


def test_review_reply_preserves_later_unread_and_updates_live_count(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    """The real browser, addressed send, runtime queue, and count must agree.

    Reuses the existing isolated Signal host. Only its backend wire is scripted.
    A component mock cannot prove bounded queue admission and live count delivery.
    """
    from tests.support.connected_rows import _message, _propose

    from planner.core.contracts import Principal, PrincipalKind

    server = signal_server
    project = api.direct_post(server, "/api/projects", {"name": "Review message proof"})
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Reply in review",
            "worker_type": "coding",
            "brief": "Approved task.",
            "project_id": project["id"],
            "manager": {"kind": "chief", "id": "chief"},
        },
    )
    identity = str(ticket["id"])
    api.direct_post(
        server,
        f"/api/tickets/{identity}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    _propose(server, identity, "The proposal remains beside the conversation.")
    conversation = "review-reply-proof"
    _post(server, f"/api/test/signals/{conversation}/start")
    _bind(server, "ticket", identity, conversation)
    sender = Principal(PrincipalKind.ticket, identity)
    _message(server, conversation, sender, "Please answer this displayed message.")
    context = context_factory()
    page = open_page(context, server, "#/review", '[data-review-card]')
    if page.locator('[data-review-item-type="proposal"]').count():
        page.get_by_role("button", name="Skip", exact=False).click()
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1000})
    card = page.locator('[data-review-item-type="message"]')
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1)
    bound = int(
        card.locator("[data-review-message-sequence]").get_attribute("data-review-message-sequence")
        or "0"
    )
    badge = page.locator("[data-review-badge]").first
    expect(badge).to_have_text("2")
    card.locator("[data-review-reply]").fill("The reply reaches this conversation once.")
    capture_success(page, "connected-review-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture_success(page, "connected-review-narrow.png")
    page.set_viewport_size({"width": 1440, "height": 1000})
    submitted: list[dict[str, object]] = []

    def newer_message_before_send(route: Route) -> None:
        submitted.append(route.request.post_data_json)
        _message(server, conversation, sender, "This newer message stays unread.")
        route.continue_()

    page.route(f"**/api/tickets/{identity}/conversation/send", newer_message_before_send, times=1)
    with page.expect_response(
        lambda response: response.url.endswith(f"/tickets/{identity}/conversation/send")
    ) as sent:
        card.locator("[data-review-send]").click()
    assert sent.value.ok, sent.value.text()
    assert sent.value.json()["fate"] == "queued"
    assert submitted[0]["owner_read_through_sequence"] == bound
    snapshot = api.get(server, f"/api/conversation/conversations/{conversation}")
    assert snapshot["owner_read_through_sequence"] == bound
    assert any(
        prompt["sender_message_id"] == submitted[0]["sender_message_id"]
        for prompt in snapshot["held_prompts"]
    )
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1)
    expect(card.locator(".review-message-run")).to_have_text("This newer message stays unread.")
    expect(badge).to_have_text("2")
    _post(server, f"/api/test/signals/{conversation}/finish")
    page.wait_for_function(
        """async ({conversation, identity}) => {
        const endpoint = `/api/conversation/conversations/${conversation}/events?after=0`;
        const response = await fetch(endpoint);
        const value = await response.json();
        const rows = Array.isArray(value) ? value : value.events;
        return rows.some(row => row.kind === 'prompt'
            && row.payload.sender_message_id === identity);
    }""",
        arg={"conversation": conversation, "identity": submitted[0]["sender_message_id"]},
        timeout=WAIT_MS,
    )
    snapshot = api.get(server, f"/api/conversation/conversations/{conversation}")
    assert snapshot["owner_read_through_sequence"] == bound
    card.locator("[data-review-mark-read]").click()
    expect(page.locator("[data-review-item-type=message]")).to_have_count(0)
    expect(badge).to_have_text("1")
    page.reload()
    expect(page.locator("[data-approval-block]")).to_be_visible()
    # A reset leaves the old unread run addressable. Open must not follow the new pointer.
    replacement = "review-replacement-proof"
    _post(server, f"/api/test/signals/{replacement}/start")
    _bind(server, "ticket", identity, replacement)
    _message(server, conversation, sender, "An unread message in the earlier conversation.")
    page.reload()
    page.locator('[data-review-item-type="proposal"] [data-skip]').click()
    historical = page.locator('[data-review-item-type="message"]')
    expect(historical).to_have_attribute("data-conversation-id", conversation)
    historical.get_by_role("link", name="Open conversation", exact=False).click()
    pane = page.locator("[data-conversation-pane]")
    expect(pane).to_have_attribute("data-conversation-id", conversation)
    expect(pane).to_have_attribute("data-conversation-read-only", "true")
    expect(pane).to_have_attribute("data-conversation-state", "opened")
