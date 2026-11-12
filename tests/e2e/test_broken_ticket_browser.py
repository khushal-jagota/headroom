"""Prove diagnostic admission through the real Workspace and demand-loaded Ticket route.

Backend/SSR tests cannot prove that navigation admits an unknown Worker record or that
ResourceState displays its diagnostic without a manifest. Reuse the existing rail host
and browser for one connected case. No models or additional server fixtures are needed.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ServerHandle
from tests.support.ticket_workflows import preset_workflow_json

from planner.core.db import connect
from planner.database_modules import attach


def test_broken_ticket_diagnostic_and_healthy_navigation(
    server_factory: Callable[..., ServerHandle],
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    tmp_path: Path,
) -> None:
    def seed(database: Path) -> None:
        with closing(connect(str(database))) as conn:
            workflow = preset_workflow_json(conn)
            conn.executemany(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at,workflow_json) VALUES (?,?,'coding','codex',"
                "'needs_brief','needs_brief','{}',1,1,?)",
                (
                    ("broken", "Broken Ticket example", workflow),
                    ("healthy", "Healthy Ticket example", workflow),
                ),
            )
        # Seed preexisting corruption through a separate raw connection. The running
        # server must record its mark before ordinary projections read this row.
        with closing(sqlite3.connect(database)) as raw:
            attach(raw)
            raw.execute("BEGIN")
            # The Ticket's own saved workflow is its stage authority, so break that.
            raw.execute(
                "UPDATE tickets SET workflow_json='{\"removed_worker\": true}' WHERE id='broken'"
            )
            raw.commit()

    server = server_factory(seed_db=seed)
    broken, healthy = "broken", "healthy"
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{broken}",
        "[data-broken-ticket]",
    )
    page.set_viewport_size({"width": 1100, "height": 900})
    diagnostic = page.locator("[data-broken-ticket]")
    expect(diagnostic.get_by_role("heading", name="Broken Ticket example")).to_be_visible()
    expect(diagnostic.locator(".error-message")).to_contain_text(
        "invalid stored Ticket structure", timeout=WAIT_MS
    )
    expect(diagnostic.locator(".error-message")).not_to_contain_text("removed_worker")
    expect(diagnostic.get_by_role("button")).to_have_count(0)
    # Admission already ran before browser sockets opened. Export screenshots through
    # the runner's report directory without repeating its inherited-descriptor check.
    reports = (
        Path(os.environ["XDG_CACHE_HOME"]).parent / "reports"
        if os.environ.get("PANELS_REQUIRE_TEST_CONTAINMENT") == "1"
        else tmp_path
    )
    page.screenshot(path=str(reports / "broken-ticket-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 430, "height": 900})
    page.screenshot(path=str(reports / "broken-ticket-mobile.png"), full_page=True)
    page.goto(server.base + f"/#/workspace/{healthy}")
    expect(page.locator('[data-screen="ticket"]')).to_be_visible(timeout=WAIT_MS)
    expect(page.locator("[data-broken-ticket]")).to_have_count(0)
    expect(
        page.locator('[data-screen="ticket"]').get_by_text("Healthy Ticket example", exact=True)
    ).to_be_visible(timeout=WAIT_MS)
