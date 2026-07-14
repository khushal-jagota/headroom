"""Browser coverage for resolved blocker context and Sprint item anchors."""

from __future__ import annotations

import httpx

WAIT_MS = 10_000


def _post_stage(server, ticket_id: str, stage: str) -> dict:
    resp = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/stage",
        json={"to_stage": stage},
        timeout=10.0,
    )
    assert resp.status_code < 300, f"POST Stage -> {resp.status_code}: {resp.text}"
    return resp.json()


def _get_ticket(server, ticket_id: str) -> dict:
    resp = httpx.get(f"{server.base}/api/tickets/{ticket_id}", timeout=10.0)
    assert resp.status_code < 300, f"GET ticket -> {resp.status_code}: {resp.text}"
    return resp.json()


def test_ticket_blocker_summary_links_and_sprint_item_hash_selection(
    server, context_factory, open_page, cli
) -> None:
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Blockers frontend sprint",
        "--date-start",
        "2026-07-01",
        "--date-end",
        "2026-07-14",
    )
    item_id = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Blocked sprint item",
        "--project",
        "Vylo",
        "--sprint",
        sprint["id"],
    )["id"]
    active_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Active blocker",
    )["id"]
    cleared_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Cleared blocker",
    )["id"]
    blocked_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Blocked ticket",
    )["id"]

    cli(server, "ticket", "block", blocked_ticket, "--by", active_blocker)
    cli(server, "ticket", "block", blocked_ticket, "--by", cleared_blocker)
    cli(server, "sprint", "item", "block", item_id, "--by", blocked_ticket)
    assert _get_ticket(server, cleared_blocker)["stage"] == "needs_success"
    _post_stage(server, cleared_blocker, "done")

    ready = f'section[data-screen="ticket"][data-ticket-id="{blocked_ticket}"]'
    page = open_page(context_factory(), server, f"#/ticket/{blocked_ticket}", ready, settled=True)

    summary = page.locator("[data-blocker-summary]")
    summary.wait_for(timeout=WAIT_MS)
    assert summary.locator('[data-blocker-group="blocked-by"]').inner_text() == (
        "BLOCKED BY\nActive blocker\nactive\nCleared blocker\ncleared"
    )
    assert summary.locator('[data-blocker-group="blocks"]').inner_text() == (
        "BLOCKS\nBlocked sprint item\nactive"
    )
    assert summary.locator(f'a[href="#/ticket/{active_blocker}"]').count() == 1
    sprint_item_link = summary.locator(f'a[href="#/sprint?item={item_id}"]')
    assert sprint_item_link.count() == 1

    sprint_item_link.click()
    page.wait_for_selector(
        f'section[data-screen="sprint"] [data-item-id="{item_id}"][data-selected="true"]',
        timeout=WAIT_MS,
    )
    page.wait_for_function(
        """itemId => {
          const row = document.querySelector(`[data-item-id="${itemId}"]`);
          return row && row === document.activeElement;
        }""",
        arg=item_id,
        timeout=WAIT_MS,
    )
