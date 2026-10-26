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
                pane.locator("[data-conversation-expand]").click()
            expect(page.locator('[data-conversation-state="opened"]')).to_be_visible()
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


def test_combined_review_approval_reply_and_bounded_read_reach_canonical_writers(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    """The combined card must preserve canonical approval, delivery, and read bounds.

    Reuses the existing isolated Signal host. Only its backend wire is scripted.
    Component/API tests cannot prove that the browser section reaches the actual
    proposal writer, backend wire, and bounded owner-read writer.
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
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1000})
    ticket_card = page.locator(f'[data-review-ticket-card][data-ticket-id="{identity}"]')
    card = ticket_card.locator('[data-review-item-type="message"]')
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1, timeout=WAIT_MS)
    bound = int(
        card.locator("[data-review-message-sequence]").get_attribute("data-review-message-sequence")
        or "0"
    )
    badge = page.locator("[data-review-badge]").first
    expect(badge).to_have_text("1")
    expect(page.locator("[data-review-ticket-card]")).to_have_count(1)
    proposal = api.get(server, f"/api/tickets?id={identity}&detail=full")["pending_proposal"]
    expect(ticket_card.locator("[data-accept]")).to_be_visible(timeout=WAIT_MS)
    capture_success(page, "connected-review-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture_success(page, "connected-review-narrow.png")
    page.set_viewport_size({"width": 1440, "height": 1000})
    ticket_card.locator("[data-accept]").click()
    ticket_card.locator('[data-ceiling-picker-choice="needs_what_changes"]').click()
    with page.expect_response(
        lambda response: response.url.endswith(f"/tickets/{identity}/accept/success_condition")
    ) as approved:
        ticket_card.locator('[data-ceiling-picker-choice="owner"]').click()
    assert approved.value.ok, approved.value.text()
    approval_body = approved.value.request.post_data_json
    assert isinstance(approval_body, dict)
    assert approval_body["expected_proposal_revision"] == proposal["revision"]
    canonical = api.get(server, f"/api/tickets?id={identity}&detail=full")
    assert canonical["field_values"]["success_condition"] == proposal["body"]
    assert canonical["pending_proposal"] is None
    expect(ticket_card.locator("[data-approval-block]")).to_have_count(0)
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1)
    expect(badge).to_have_text("1")
    card.locator("[data-review-reply]").fill("The reply reaches this conversation once.")
    submitted: list[dict[str, object]] = []

    def newer_message_before_send(route: Route) -> None:
        body = route.request.post_data_json
        assert isinstance(body, dict)
        submitted.append(body)
        _message(server, conversation, sender, "This newer message stays unread.")
        route.continue_()

    page.route(f"**/api/tickets/{identity}/conversation/send", newer_message_before_send, times=1)
    with page.expect_response(
        lambda response: response.url.endswith(f"/tickets/{identity}/conversation/send")
    ) as sent:
        card.locator("[data-review-send]").click()
    assert sent.value.ok, sent.value.text()
    assert sent.value.json()["fate"] == "queued"
    assert submitted[0]["conversation_id"] == conversation
    assert submitted[0]["owner_read_through_sequence"] == bound
    snapshot = api.get(server, f"/api/conversation/conversations/{conversation}")
    assert snapshot["owner_read_through_sequence"] == bound
    assert any(
        prompt["sender_message_id"] == submitted[0]["sender_message_id"]
        for prompt in snapshot["held_prompts"]
    )
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1)
    expect(card.locator(".review-message-run")).to_have_text("This newer message stays unread.")
    expect(badge).to_have_text("1")
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
    # A durable prompt row is admission. This receipt proves backend delivery.
    receipts = page.evaluate(
        """async ({conversation, timeout}) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
            const response = await fetch(`/api/test/signals/${conversation}/delivered`);
            const receipt = await response.json();
            const reply = 'The reply reaches this conversation once.';
            if (receipt.texts.some(text => text.includes(reply))) return receipt.texts;
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        throw new Error('The exact reply did not reach the scripted backend');
    }""", {"conversation": conversation, "timeout": WAIT_MS},
    )
    assert sum("The reply reaches this conversation once." in text for text in receipts) == 1
    displayed_bound = int(card.locator("[data-review-message-sequence]").get_attribute(
        "data-review-message-sequence",
    ) or "0")
    reads: list[dict[str, object]] = []

    def newer_message_before_read(route: Route) -> None:
        body = route.request.post_data_json
        assert isinstance(body, dict)
        reads.append(body)
        _message(
            server, conversation, sender, "This message arrives after the displayed read boundary.",
        )
        route.continue_()

    page.route(
        f"**/api/conversation/conversations/{conversation}/owner-read",
        newer_message_before_read, times=1,
    )
    with page.expect_response(
        lambda response: response.url.endswith(f"/conversations/{conversation}/owner-read")
    ) as read:
        card.locator("[data-review-mark-read]").click()
    assert read.value.ok, read.value.text()
    assert reads == [{"through_sequence": displayed_bound}]
    snapshot = api.get(server, f"/api/conversation/conversations/{conversation}")
    assert snapshot["owner_read_through_sequence"] == displayed_bound
    expect(card.locator("[data-review-message-sequence]")).to_have_count(1)
    expect(card.locator(".review-message-run")).to_have_text(
        "This message arrives after the displayed read boundary.",
    )
    expect(badge).to_have_text("1")
    card.locator("[data-review-mark-read]").click()
    expect(page.locator("[data-review-item-type=message]")).to_have_count(0)
    expect(page.locator("[data-review-ticket-card]")).to_have_count(0)
    # A reset leaves the old unread run addressable. Open must not follow the new pointer.
    replacement = "review-replacement-proof"
    _post(server, f"/api/test/signals/{replacement}/start")
    _bind(server, "ticket", identity, replacement)
    _message(server, conversation, sender, "An unread message in the earlier conversation.")
    page.reload()
    historical = page.locator('[data-review-item-type="message"]')
    expect(historical).to_have_attribute("data-conversation-id", conversation)
    historical.get_by_role("link", name="Open conversation", exact=False).click()
    pane = page.locator("[data-conversation-pane]")
    expect(pane).to_have_attribute("data-conversation-id", conversation)
    expect(pane).to_have_attribute("data-conversation-read-only", "true")
    expect(pane).to_have_attribute("data-conversation-state", "opened")


def test_history_refused_row_keeps_unread_credit_until_latest_delivery(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Real tail refusal must constrain canonical read credit and unread projections.

    Reuse the Signal host. Component tests cannot prove that a refused tail row
    stays unread through the actual owner-read writer, navigation projection,
    paging reconnect, and Latest delivery. The connected case costs about 25 seconds.
    """
    server = signal_server
    conversation = "history-read-credit-proof"
    ticket = cli(server, "ticket", "create", "--worker-type", "coding",
                 "--title", "History read credit proof")["id"]
    _post(server, f"/api/test/signals/{conversation}/start")
    _bind(server, "ticket", ticket, conversation)
    body = " ".join(f"line{i} of a worker report that is as long as a real one"
                    for i in range(90))
    for index in range(160):
        _post(server, f"/api/test/signals/{conversation}/message", {
            "label": "Worker", "kind": "ticket", "id": ticket,
            "text": f"report {index}: {body}",
        })
    _post(server, f"/api/test/signals/{conversation}/finish")
    context = context_factory()
    context.add_init_script("""window.creditFrames=[];window.creditStreams=0;
      window.creditPendingReads=0;
      const Original=window.EventSource;
      window.EventSource=class extends Original {
        constructor(url,options) {super(url,options);
          if(String(url).includes('/view/tail')) window.creditStreams++;
          this.addEventListener('changes',event=>{
            window.creditFrames.push(JSON.parse(event.data));
          });
        }
      };
      const fetchOriginal=window.fetch;
      window.fetch=async (...args)=>{
        const url=typeof args[0]==='string'?args[0]:args[0].url;
        const read=url.includes('/owner-read');
        if(read)window.creditPendingReads++;
        try{return await fetchOriginal(...args);}
        finally{if(read)window.creditPendingReads--;}
      };""")
    page = open_page(context, server, f"#/workspace/{ticket}",
                     f'[data-screen="ticket"][data-ticket-id="{ticket}"]')
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-lens-choice="focus"]').click(timeout=WAIT_MS)
    thread = page.locator("[data-conversation-thread]")
    expect(thread).to_have_attribute("data-conversation-placement-ready", "true", timeout=WAIT_MS)
    assert page.evaluate("document.visibilityState === 'visible' && document.hasFocus()")

    def snapshot() -> JsonObject:
        response = httpx.get(server.base + f"/api/conversation/conversations/{conversation}",
                             timeout=10)
        assert response.is_success, response.text
        result: JsonObject = response.json()
        return result

    def wait_read(sequence: int) -> None:
        page.wait_for_function("""async ({id,sequence}) => {
          const response=await fetch(`/api/conversation/conversations/${id}`);
          return (await response.json()).owner_read_through_sequence===sequence;
        }""", arg={"id": conversation, "sequence": sequence}, timeout=WAIT_MS)

    def anchor() -> object:
        return thread.evaluate("""t=>{
          const frame=t.getBoundingClientRect();
          const row=[...t.querySelectorAll('[data-conversation-view-key]')].find(n=>{
            const b=n.getBoundingClientRect();return b.bottom>frame.top&&b.top<frame.bottom;
          });return row?{key:row.dataset.conversationViewKey,
            offset:row.getBoundingClientRect().top-frame.top}:null;
        }""")

    def arrive(text: str) -> int:
        _post(server, f"/api/test/signals/{conversation}/message", {
            "label": "Worker", "kind": "ticket", "id": ticket, "text": text,
        })
        sequence = int(snapshot()["latest_sequence"])
        page.wait_for_function("""sequence=>window.creditFrames.some(frame=>
          frame.coverage.through_sequence>=sequence&&frame.upserts.some(row=>
            row.source_sequence===sequence)&&frame.scope_changes.some(scope=>
            scope.scope.kind==='timeline'&&scope.item_keys.includes(`e${sequence}`)))
        """, arg=sequence, timeout=WAIT_MS)
        # Settle the Svelte effect and any owner-read fetch it starts.
        page.evaluate("""()=>new Promise(resolve=>requestAnimationFrame(()=>
          requestAnimationFrame(resolve)))""")
        page.wait_for_function("window.creditPendingReads===0", timeout=WAIT_MS)
        return sequence

    def unread_projection(expected: bool) -> None:
        board = httpx.get(server.base + "/api/board", timeout=10).json()
        card = next(card for column in board["columns"] for card in column["cards"]
                    if card["id"] == ticket)
        assert card["conversation_signal"]["message_for_owner"] is expected
        assert card["awaiting_reply"] is expected
        navigation = httpx.get(server.base + "/api/navigation/snapshot", timeout=10).json()
        record = next(row["value"] for row in navigation["snapshot"]["records"]
                      if row["kind"] == "conversation" and row["id"] == conversation)
        assert record["attention"]["durable"]["message_for_owner"] is expected
        assert record["attention"]["current"]["signal"]["message_for_owner"] is expected
        review = httpx.get(server.base + "/api/review", timeout=10).json()
        assert sum(item.get("review_item_type") == "message"
                   and item.get("conversation_id") == conversation
                   for item in review["items"]) == int(expected)
        badge = page.locator(".rail-foot [data-review-badge]")
        visible_marks = page.locator('[data-conversation-signal="message"]:visible')
        if expected:
            expect(visible_marks.first).to_be_visible(timeout=WAIT_MS)
            expect(badge).to_have_text("1", timeout=WAIT_MS)
            expect(badge).to_be_visible(timeout=WAIT_MS)
        else:
            expect(visible_marks).to_have_count(0, timeout=WAIT_MS)
            expect(badge).to_have_text("", timeout=WAIT_MS)
            expect(badge).not_to_be_visible(timeout=WAIT_MS)

    def latest(text: str, sequence: int) -> None:
        page.get_by_role("button", name="Jump to latest message", exact=True).first.click(
            timeout=WAIT_MS,
        )
        expect(thread).to_contain_text(text, timeout=WAIT_MS)
        page.wait_for_function("""()=>{const t=document.querySelector('[data-conversation-thread]');
          return t.scrollHeight-t.clientHeight-t.scrollTop<4;}""", timeout=WAIT_MS)
        wait_read(sequence)
        unread_projection(False)

    initial = int(snapshot()["latest_sequence"])
    wait_read(initial)  # Preserve the existing initial-entry read-credit behavior.
    before = anchor()
    first = "Unread until Latest delivers this refused history row."
    first_sequence = arrive(first)
    assert first not in thread.inner_text()
    assert anchor() == before
    expect(page.get_by_role("button", name="Jump to latest message", exact=True).first
           ).to_be_visible(timeout=WAIT_MS)
    actual = int(snapshot()["owner_read_through_sequence"])
    print(f"READ_CREDIT_REFUSAL initial={initial} unique={first_sequence} actual={actual}")
    assert actual == initial, "A refused row must not advance canonical owner read credit"
    unread_projection(True)
    latest(first, first_sequence)

    streams = page.evaluate("window.creditStreams")
    thread.hover()
    page.mouse.wheel(0, -100000)
    page.wait_for_function("before=>window.creditStreams>before", arg=streams, timeout=WAIT_MS)
    expect(thread).to_contain_text("report 0:", timeout=WAIT_MS)
    before = anchor()
    second = "Unread after a real history page and tail reconnect."
    second_sequence = arrive(second)
    assert second not in thread.inner_text()
    assert anchor() == before
    assert int(snapshot()["owner_read_through_sequence"]) == first_sequence
    unread_projection(True)
    latest(second, second_sequence)
    final = "A following reader admits this next row without a rebuild."
    final_sequence = arrive(final)
    expect(thread).to_contain_text(final, timeout=WAIT_MS)
    wait_read(final_sequence)
    unread_projection(False)
