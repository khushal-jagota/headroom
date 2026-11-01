"""Prove diagnostic admission through the real Workspace and demand-loaded Ticket route.

Backend/SSR tests cannot prove that navigation admits an unknown Worker record or that
ResourceState displays its diagnostic without a manifest. Reuse the existing rail host
and browser for one connected case. No models or additional server fixtures are needed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from test_containment.admission import requested_containment
from tests.e2e.harness import WAIT_MS, ServerHandle

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
            conn.executemany(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at) VALUES (?,?,'coding','codex',"
                "'needs_brief','needs_brief','{}',1,1)",
                (("broken", "Broken Ticket example"), ("healthy", "Healthy Ticket example")),
            )
        # Seed preexisting corruption through a separate raw connection. The running
        # server must record its mark before ordinary projections read this row.
        with closing(sqlite3.connect(database)) as raw:
            attach(raw)
            raw.execute("BEGIN")
            raw.execute("UPDATE tickets SET worker_type='removed_worker' WHERE id='broken'")
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
    expect(diagnostic.locator(".error-message")).to_contain_text("removed_worker", timeout=WAIT_MS)
    expect(diagnostic.get_by_role("button")).to_have_count(0)
    boundary = requested_containment()
    reports = boundary.scratch_root / "reports" if boundary else tmp_path
    page.screenshot(path=str(reports / "broken-ticket-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 430, "height": 900})
    page.screenshot(path=str(reports / "broken-ticket-mobile.png"), full_page=True)
    page.goto(server.base + f"/#/workspace/{healthy}")
    expect(page.locator('[data-screen="ticket"]')).to_be_visible(timeout=WAIT_MS)
    expect(page.locator("[data-broken-ticket]")).to_have_count(0)
    expect(
        page.locator('[data-screen="ticket"]').get_by_text("Healthy Ticket example", exact=True)
    ).to_be_visible(timeout=WAIT_MS)
