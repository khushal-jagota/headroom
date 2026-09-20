"""Browser-to-database proof for removing one Ticket blocker."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def test_ticket_detail_removes_a_blocker_and_settles_status(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    blocker_id = str(
        cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            "Browser blocker",
        )["id"]
    )
    blocked_id = str(
        cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            "Browser blocked Ticket",
        )["id"]
    )
    api.direct_put(server, f"/api/collections/blockers/{blocked_id}/{blocker_id}", {})

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{blocked_id}",
        f'[data-screen="ticket"][data-ticket-id="{blocked_id}"]',
    )
    chip = page.locator(f'[data-blocker-chip="{blocker_id}"]')
    chip.wait_for(timeout=WAIT_MS)
    page.locator(f'[data-remove-blocker="{blocker_id}"]').click()
    chip.wait_for(state="detached", timeout=WAIT_MS)

    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ticket_blocks WHERE blocking_ticket_id = ? "
            "AND blocked_ticket_id = ?",
            (blocker_id, blocked_id),
        ).fetchone()[0] == 0
        # Nothing was written to un-block it. The row is gone, so the answer changed.
        assert conn.execute(
            "SELECT worker_step_claim FROM tickets WHERE id = ?", (blocked_id,)
        ).fetchone()[0] == "none"
