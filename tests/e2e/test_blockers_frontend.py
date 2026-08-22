"""Browser coverage for derived blocker placement, detail, and removal."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000


def _post_stage(server: ServerHandle, ticket_id: str, stage: str) -> JsonObject:
    resp = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/stage",
        json={"to_stage": stage},
        timeout=10.0,
    )
    assert resp.status_code < 300, f"POST Stage -> {resp.status_code}: {resp.text}"
    body: JsonObject = resp.json()
    return body


def _get_ticket(server: ServerHandle, ticket_id: str) -> JsonObject:
    resp = httpx.get(f"{server.base}/api/tickets/{ticket_id}", timeout=10.0)
    assert resp.status_code < 300, f"GET ticket -> {resp.status_code}: {resp.text}"
    body: JsonObject = resp.json()
    return body


def test_workspace_holds_every_group_and_a_live_blocker_moves_a_row_between_them(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """A blocking link decides which group a row is in, and the rail carries them all.

    A Ticket waiting on its kickoff and a Ticket waiting on a later approval are two
    groups. A blocked, resting, or finished Ticket is in its own group too — the rail
    holds every group a Ticket lands in, and shuts the quiet ones. The point of doing
    this in a browser is that a live blocker moves a row between groups without a
    reload. Which groups arrive open is the rail's choice, so this test reads
    membership and never the fold.
    """
    blocker = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Prerequisite"
    )["id"]
    kickoff_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Kickoff dependent",
        "--kickoff-note",
        "Pending kickoff",
        "--blocked-by",
        blocker,
    )["id"]
    later_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Later dependent",
        "--blocked-by",
        blocker,
    )["id"]
    shared_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Shared dependent",
        "--blocked-by",
        blocker,
    )["id"]
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = 'needs_plan', ticket_status = 'awaiting_approval' "
            "WHERE id = ?",
            (later_dependent,),
        )
        conn.execute(
            "UPDATE tickets SET stage = 'needs_approach', ticket_status = 'blocked' WHERE id = ?",
            (shared_dependent,),
        )
    for ticket_id in (blocker, kickoff_dependent, later_dependent, shared_dependent):
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    page = open_page(
        context_factory(),
        server,
        "#/workspace?view=tickets",
        'section[data-screen="workspace"]',
    )
    active_card = f'[data-card][data-ticket-id="{blocker}"]'
    later_card = f'[data-card][data-ticket-id="{later_dependent}"]'
    kickoff_card = f'[data-card][data-ticket-id="{kickoff_dependent}"]'
    shared_card = f'[data-card][data-ticket-id="{shared_dependent}"]'

    # The two Tickets that want the reader sit in their own groups: a kickoff approval
    # and a later approval are not the same waiting.
    page.wait_for_selector(
        f'[data-bucket-key="waiting_for_kickoff"] {kickoff_card}',
        state="attached",
        timeout=WAIT_MS,
    )
    assert page.locator(f'[data-bucket-key="awaiting_approval"] {later_card}').count() == 1

    # The quiet ones are in the rail too, each in the group its own status names.
    assert page.locator(f'[data-bucket-key="empty"] {active_card}').count() == 1
    assert page.locator(f'[data-bucket-key="blocked"] {shared_card}').count() == 1

    # Their own status and stage remain intact behind the rail's choice.
    assert page.get_attribute(later_card, "data-ticket-status") == "awaiting_approval"
    assert page.get_attribute(kickoff_card, "data-ticket-status") == "awaiting_approval"
    assert page.get_attribute(later_card, "data-ticket-stage") == "needs_plan"
    assert page.get_attribute(
        f"{later_card} .board-workspace-stage-mark", "data-agent-working"
    ) == "false"
    assert _get_ticket(server, shared_dependent)["ticket_status"] == "blocked"
    assert _get_ticket(server, shared_dependent)["stage"] == "needs_approach"

    # Removing the last live blocker settles that row back to empty, and the row moves
    # from Blocked to Empty without a reload.
    cli(server, "ticket", "unblock", shared_dependent, "--by", blocker)
    page.wait_for_selector(
        f'[data-bucket-key="empty"] {shared_card}', state="attached", timeout=WAIT_MS
    )
    assert page.locator(f'[data-bucket-key="blocked"] {shared_card}').count() == 0
    assert _get_ticket(server, shared_dependent)["ticket_status"] == "empty"

    # A finished Ticket moves to Done the same way, and is still its own group.
    _post_stage(server, blocker, "done")
    page.wait_for_selector(
        f'[data-bucket-key="done"] {active_card}', state="attached", timeout=WAIT_MS
    )
    assert page.locator(f'[data-bucket-key="empty"] {active_card}').count() == 0
    # The Tickets that do want the reader are untouched by all of it.
    assert page.locator(kickoff_card).count() == 1
    assert page.locator(later_card).count() == 1
    assert _get_ticket(server, later_dependent)["stage"] == "needs_plan"
