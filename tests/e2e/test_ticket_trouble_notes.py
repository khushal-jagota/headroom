"""Live proof for CLI commit to visible Ticket trouble notes.

The material risk is the cross-process path: a worker CLI commit must emit an SSE
change, invalidate the mounted Ticket query, and show the appended notes without a
manual browser refresh.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle

from planner.core.db import connect


def _claim_worker_step(server: ServerHandle, ticket_id: str) -> None:
    """Enter the durable claim state without starting a real agent in this browser test."""
    conn = connect(str(server.db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
            (ticket_id,),
        )
    finally:
        conn.close()


def test_worker_cli_trouble_notes_refresh_open_ticket_in_order(
    tmp_path: Path,
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "A run with recorded trouble",
    )["id"]
    _claim_worker_step(server, ticket_id)
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ready)
    assert page.locator("[data-ticket-trouble-notes]").count() == 0

    cli(
        server,
        "worker",
        "trouble",
        "--body-file",
        "-",
        ticket_id=ticket_id,
        actor="worker",
        stdin="Harness dropped the first output.\n",
    )
    page.wait_for_selector("[data-trouble-note='1']", timeout=WAIT_MS)

    cli(
        server,
        "worker",
        "trouble",
        "--body-file",
        "-",
        ticket_id=ticket_id,
        actor="worker",
        stdin="Tool timed out on the retry.\n",
    )
    page.wait_for_selector("[data-trouble-note='2']", timeout=WAIT_MS)

    notes = page.locator("[data-ticket-trouble-notes] li")
    assert notes.count() == 2
    assert notes.nth(0).locator("p").inner_text() == "Harness dropped the first output."
    assert notes.nth(1).locator("p").inner_text() == "Tool timed out on the retry."
    assert notes.nth(0).locator("time").inner_text()
    assert notes.nth(1).locator("time").inner_text()

    evidence = Path(
        os.environ.get(
            "PANELS_TROUBLE_EVIDENCE_PATH",
            str(tmp_path / "ticket-trouble-notes.png"),
        )
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence), full_page=True)
    assert evidence.stat().st_size > 0
