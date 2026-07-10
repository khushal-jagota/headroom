"""E2E for the redesigned Backlog + Ideas compose flows.

Backlog: the dormant compose creates an item that lands in the correct priority group
via the WS-invalidation refetch (no optimistic UI), stating project — not priority — on
the row. Ideas: the always-open capture saves a title-only idea as a FLAT row (no
chevron) and a bodied idea as a disclosure that expands to reveal the body.

Every test runs against an empty DB (the default ``server`` fixture, unseeded), so there
is no since=0 catch-up flush to race the first interaction. Standalone: imports nothing
from tests/unit; every Playwright wait carries WAIT_MS; no bare sleeps.
"""

from __future__ import annotations

from playwright.sync_api import Page

WAIT_MS = 10_000


def _texts(page: Page, selector: str) -> list[str]:
    return page.eval_on_selector_all(selector, "els => els.map(e => e.textContent)")


def test_backlog_create_lands_in_priority_group(server, context_factory, open_page):
    ctx = context_factory()
    # Empty backlog: the compose is the ready gate (rendered synchronously). settled=False
    # is safe — an unseeded DB has no prior events, so no flush fires before the create.
    page = open_page(
        ctx, server, "#/backlog", '[data-screen="backlog"] details.disclosure--make', settled=False
    )

    # Open the dormant compose, fill it, choose Tribe / P1 via the chip toggles.
    page.click('[data-screen="backlog"] details.disclosure--make > summary')
    page.fill('[data-create="item"] [data-input="title"]', "Wire the audit log")
    page.click('[data-create="item"] [data-seg="project"] [data-value="project_tribe"]')
    page.click('[data-create="item"] [data-seg="priority"] [data-value="P1"]')
    page.click('[data-create="item"] [data-commit]')

    # The WS flush re-renders; the new item appears under its priority group.
    page.wait_for_selector('[data-priority-group="P1"] [data-item-id]', timeout=WAIT_MS)
    assert _texts(page, '[data-priority-group="P1"] [data-item-id] .list-row-title') == [
        "Wire the audit log"
    ]
    # The row states project, not priority.
    row = page.eval_on_selector('[data-priority-group="P1"] [data-item-id]', "e => e.textContent")
    assert "Tribe" in row
    assert "P1" not in row
    # It landed in exactly one group.
    assert len(_texts(page, "[data-backlog-items] [data-item-id]")) == 1


def test_ideas_capture_flat_and_disclosure(server, context_factory, open_page):
    ctx = context_factory()
    page = open_page(ctx, server, "#/ideas", '[data-screen="ideas"] .capture', settled=False)

    # A title-only capture → a FLAT row with no disclosure.
    page.fill('[data-create="idea"] [data-input="title"]', "Dark mode only, skip the light theme")
    page.click('[data-create="idea"] [data-commit]')
    page.wait_for_selector('[data-ideas] div.list-row[data-idea-id]', timeout=WAIT_MS)
    flat = page.query_selector('[data-ideas] div.list-row[data-idea-id]')
    assert "Dark mode only, skip the light theme" in flat.text_content()
    # Title-only means no chevron to expand: no <details> disclosure exists yet.
    assert page.query_selector('[data-ideas] details.disclosure--idea') is None

    # A bodied capture → a disclosure (newest-first, so it is the first row).
    page.fill('[data-create="idea"] [data-input="title"]', "One-question onboarding")
    page.fill(
        '[data-create="idea"] [data-input="body"]',
        "Ask one thing that matters, infer the rest.",
    )
    page.click('[data-create="idea"] [data-commit]')
    page.wait_for_selector('[data-ideas] details.disclosure--idea[data-idea-id]', timeout=WAIT_MS)

    details = page.query_selector('[data-ideas] details.disclosure--idea[data-idea-id]')
    assert "One-question onboarding" in details.query_selector(".it").text_content()
    # The body is present in the DOM but the disclosure starts closed.
    assert "Ask one thing that matters" in details.query_selector(".disclosure-body").text_content()
    assert details.get_attribute("open") is None

    # Expanding the chevron opens the disclosure.
    page.click('[data-ideas] details.disclosure--idea > summary')
    page.wait_for_selector('[data-ideas] details.disclosure--idea[open]', timeout=WAIT_MS)

    # The title-only idea is still flat below it.
    assert page.query_selector('[data-ideas] div.list-row[data-idea-id]') is not None
