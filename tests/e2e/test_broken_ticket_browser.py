"""Prove diagnostic admission through the real Workspace and demand-loaded Ticket route.

Backend/SSR tests cannot prove that navigation admits an unknown Worker record or that
ResourceState displays its diagnostic without a manifest. Reuse the existing rail host
and browser for one connected case. No models or additional server fixtures are needed.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from test_containment.admission import requested_containment
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.server_fixtures import rail_server  # noqa: F401

from planner.core.db import connect


def test_broken_ticket_diagnostic_and_healthy_navigation(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    tmp_path: Path,
) -> None:
    server = rail_server
    manager = api.direct_post(
        server, "/api/items", {"title": "Ticket isolation proof", "project_id": "project_panels"}
    )
    tickets = [
        api.direct_post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "orchestrator": {"kind": "sprint_item", "id": manager["id"]},
            },
        )
        for title in ("Broken Ticket example", "Healthy Ticket example")
    ]
    broken, healthy = (str(ticket["id"]) for ticket in tickets)
    with closing(connect(str(server.db_path))) as conn:
        conn.execute("UPDATE tickets SET worker_type='removed_worker' WHERE id=?", (broken,))
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{manager['id']}/{broken}",
        "[data-broken-ticket]",
    )
    page.set_viewport_size({"width": 1100, "height": 900})
    diagnostic = page.locator("[data-broken-ticket]")
    expect(diagnostic.get_by_role("heading", name="Broken Ticket example")).to_be_visible()
    expect(diagnostic.get_by_role("alert")).to_contain_text("removed_worker", timeout=WAIT_MS)
    expect(diagnostic.get_by_role("button")).to_have_count(0)
    boundary = requested_containment()
    reports = boundary.scratch_root / "reports" if boundary else tmp_path
    page.screenshot(path=str(reports / "broken-ticket-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 430, "height": 900})
    page.screenshot(path=str(reports / "broken-ticket-mobile.png"), full_page=True)
    page.goto(server.base + f"/#/workspace/item/{manager['id']}/{healthy}")
    expect(page.locator('[data-screen="ticket"]')).to_be_visible(timeout=WAIT_MS)
    expect(page.locator("[data-broken-ticket]")).to_have_count(0)
    expect(
        page.locator('[data-screen="ticket"]').get_by_text("Healthy Ticket example", exact=True)
    ).to_be_visible(timeout=WAIT_MS)
