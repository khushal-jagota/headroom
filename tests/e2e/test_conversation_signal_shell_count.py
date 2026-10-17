"""Prove actual conversation work reaches the existing shell count through SSE.

A frontend component test cannot prove settled runtime publication, deduplication
across associated records, or a mounted shell refresh without a manual reload.
The existing controlled-adapter fixture uses the real conversation core and no provider.
"""
from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e import test_conversation_signals as signal_proof
from tests.e2e.harness import REPO_ROOT, WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_conversation_signals import (  # noqa: F401
    _post,
    signal_server,
)

from planner.core.db import connect


@pytest.fixture
def shell_signal_server(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> ServerHandle:
    monkeypatch.setattr(
        signal_proof, "EVIDENCE",
        REPO_ROOT / "data/verification/conversation-signals/shell-count-8fd01bb8",
    )
    result: ServerHandle = request.getfixturevalue("signal_server")
    return result


@pytest.mark.parametrize("layout", ["desktop"])
def test_shell_counts_actual_unique_conversations_without_worker_claims(
    shell_signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    request: pytest.FixtureRequest,
    layout: str,
) -> None:
    assert layout == "desktop"
    server = shell_signal_server
    evidence = server.db_path.parent / "shell-count-evidence"
    evidence.mkdir()
    project = api.direct_post(
        server, "/api/projects", {"name": "Shell count proof", "priority": "P1"},
    )
    item = api.direct_post(server, "/api/items", {
        "title": "Count supervisor", "project_id": project["id"],
    })
    tickets = [api.direct_post(server, "/api/tickets", {
        "title": title, "worker_type": "coding", "kickoff_note": "Bounded count proof.",
        "orchestrator": {"kind": "sprint_item", "id": item["id"]},
    }) for title in ("Count Worker", "Duplicate association to same conversation")]
    # Match the existing mark proof's real parked owner-proposal fixture: rows are
    # visible because of actual pending work, independently of conversation runtime.
    for ticket in tickets:
        api.direct_post(server, f"/api/tickets/{ticket['id']}/accept/brief", {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        })
        proposed = httpx.post(
            server.base + f"/api/tickets/{ticket['id']}/propose",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
            json={"body": "Quiet approval remains separate.", "recap": "Shell count proof."},
            timeout=10,
        )
        assert proposed.is_success, proposed.text
    identities = {
        "proof-worker": {"kind": "ticket", "id": tickets[0]["id"], "label": "Count Worker"},
        "proof-supervisor": {
            "kind": "sprint_item", "id": item["id"], "label": "Count supervisor",
        },
        "proof-chief": {"kind": "chief", "id": "chief", "label": "Chief of Staff"},
    }
    for conversation in identities:
        _post(server, f"/api/test/signals/{conversation}/start")
        _post(server, f"/api/test/signals/{conversation}/finish")
    with connect(str(server.db_path)) as conn:
        for ticket in tickets:
            conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?",
                         ("proof-worker", ticket["id"]))
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("proof-worker", tickets[0]["id"]),
        )
        conn.execute("UPDATE agents SET conversation_id=? WHERE agent_key=?",
                     ("proof-supervisor", item["supervisor"]["agent_key"]))
        conn.execute("INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) "
                     "ON CONFLICT(agent_key) DO UPDATE SET "
                     "conversation_id=excluded.conversation_id", ("proof-chief",))
        claims = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE worker_step_claim='out'",
        ).fetchone()
        assert claims is not None and claims[0] == 0
        # An intentionally seeded claim proves claim-only activity cannot count as work.
        conn.execute("UPDATE tickets SET worker_step_claim='out' WHERE id=?", (tickets[0]["id"],))
        conn.commit()
    context = context_factory()
    with context.expect_event(
        "response", predicate=lambda response: response.url.endswith("/api/review"),
        timeout=WAIT_MS,
    ) as initial_review:
        page = open_page(context, server, "#/workspace", '[data-screen="workspace"]')
    initial_snapshot = initial_review.value.json()
    assert initial_snapshot["running_worker_count"] == 0
    page.set_viewport_size({"width": 1440, "height": 1000})

    def capture_final_state() -> None:
        page.screenshot(path=str(evidence / "final-state.png"), full_page=True)
        (evidence / "final-dom.html").write_text(page.content())

    request.addfinalizer(capture_final_state)
    presence = page.locator("[data-shell-presence]")
    expect(presence).to_have_text("0 working", timeout=WAIT_MS)
    page.evaluate("""() => {
        window.shellCountProofEvents = [];
        window.shellCountProofStream = new EventSource('/api/changes');
        window.shellCountProofStream.onmessage = event =>
            window.shellCountProofEvents.push(event.data);
    }""")
    page.wait_for_function("() => window.shellCountProofStream.readyState === 1", timeout=WAIT_MS)
    observations: list[JsonObject] = [{
        "claim_only": True, "out_claims": 1, "review_snapshot": initial_snapshot, "shell_count": 0,
    }]
    for conversation in identities:
        expect(page.locator(f'[data-conversation-id="{conversation}"] '
                            '[data-conversation-signal="working"]')).to_have_count(0)
    page.screenshot(path=str(evidence / "claim-only-zero.png"), full_page=True)
    with connect(str(server.db_path)) as conn:
        conn.execute("UPDATE tickets SET worker_step_claim='none' WHERE id=?", (tickets[0]["id"],))
        conn.commit()

    def transition(conversation: str, operation: str, expected_count: int) -> None:
        before = page.evaluate("window.shellCountProofEvents.length")
        with page.expect_response("**/api/review", timeout=WAIT_MS) as review_response:
            _post(server, f"/api/test/signals/{conversation}/{operation}")
        review_snapshot = review_response.value.json()
        assert review_snapshot["running_worker_count"] == expected_count
        # Only the application's mounted query/subscription may refresh the shell.
        expect(presence).to_have_text(f"{expected_count} working", timeout=WAIT_MS)
        expect(presence.locator(".shell-presence-spin")).to_have_count(int(expected_count > 0))
        page.wait_for_function(
            "before => window.shellCountProofEvents.length > before", arg=before, timeout=WAIT_MS,
        )
        observations.append({"conversation": conversation, "operation": operation,
                             "shell_count": expected_count, "review_snapshot": review_snapshot})
        marks = page.locator(f'[data-conversation-id="{conversation}"]').first
        expect(marks.locator('[data-conversation-signal="working"]')).to_have_count(
            int(operation == "start"),
        )
        if operation == "start" and expected_count == 1:
            expect(page.locator(f'[data-conversation-id="{conversation}"]').first).to_be_visible()
            page.screenshot(path=str(evidence / f"{conversation}-one-working.png"), full_page=True)

    # Each of the three real conversation roles counts without a Worker-step claim.
    for conversation in identities:
        transition(conversation, "start", 1)
        transition(conversation, "finish", 0)
    # Two Worker records share one conversation: the first start still counts only one.
    for count, conversation in enumerate(identities, start=1):
        transition(conversation, "start", count)
    page.screenshot(path=str(evidence / "three-roles-working.png"), full_page=True)
    for count, conversation in zip((2, 1, 0), identities, strict=True):
        transition(conversation, "finish", count)
    for conversation, identity in identities.items():
        before = page.evaluate("window.shellCountProofEvents.length")
        with page.expect_response("**/api/review", timeout=WAIT_MS) as message_response:
            _post(server, f"/api/test/signals/{conversation}/message", identity)
        message_snapshot = message_response.value.json()
        assert message_snapshot["running_worker_count"] == 0
        page.wait_for_function(
            "before => window.shellCountProofEvents.length > before", arg=before, timeout=WAIT_MS,
        )
        expect(presence).to_have_text("0 working", timeout=WAIT_MS)
        view = api.get(server, f"/api/conversation/conversations/{conversation}")
        with page.expect_response("**/api/review", timeout=WAIT_MS) as read_response:
            _post(server, f"/api/conversation/conversations/{conversation}/owner-read", {
                "through_sequence": view["latest_sequence"],
            })
        read_snapshot = read_response.value.json()
        assert read_snapshot["running_worker_count"] == 0
        expect(presence).to_have_text("0 working", timeout=WAIT_MS)
        observations.append({"conversation": conversation, "message_only_count": 0,
                             "after_read_count": 0, "message_review": message_snapshot,
                             "read_review": read_snapshot})
    events = page.evaluate("window.shellCountProofEvents")
    assert events and set(events) == {"change"}, events
    page.screenshot(path=str(evidence / "zero-after-message-read.png"), full_page=True)
    (evidence / "observations.json").write_text(json.dumps({
        "observations": observations, "change_events": events,
        "worker_claim_count": 0, "associated_worker_records": 2,
        "unique_conversations": 3, "manual_reload_or_query_refetch": False,
        "backend": "Controlled adapter over real conversation core; no provider execution.",
        "server": server.base, "db_path": str(server.db_path),
    }, indent=2))
    page.evaluate("window.shellCountProofStream.close()")
    (server.db_path.parent / "complete.json").write_text(json.dumps({"proof": "shell count"}))
