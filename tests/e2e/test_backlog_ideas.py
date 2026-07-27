"""E2E for the redesigned Backlog + Ideas compose flows.

Backlog: the dormant compose creates an item that lands in the correct priority group
via the write's own refetch (no optimistic UI), stating project — not priority — on
the row. Ideas: the always-open capture saves a title-only idea as a FLAT row (no
chevron) and a bodied idea as a disclosure that expands to reveal the body.

Every test runs against an empty DB (the default ``server`` fixture, unseeded).
Standalone: imports nothing from tests/unit; every Playwright wait carries WAIT_MS;
no bare sleeps.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000


def _texts(page: Page, selector: str) -> list[str]:
    texts: list[str] = page.eval_on_selector_all(selector, "els => els.map(e => e.textContent)")
    return texts


def test_backlog_create_lands_in_priority_group(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    ctx = context_factory()
    # Empty backlog: the compose is the ready gate (rendered synchronously).
    page = open_page(
        ctx, server, "#/backlog", '[data-screen="backlog"] details.disclosure--make'
    )

    # Open the dormant compose, fill it, choose Tribe / P1 via the chip toggles.
    page.click('[data-screen="backlog"] details.disclosure--make > summary')
    page.fill('[data-create="item"] [data-input="title"]', "Wire the audit log")
    page.click('[data-create="item"] [data-seg="project"] [data-value="project_tribe"]')
    page.click('[data-create="item"] [data-seg="priority"] [data-value="P1"]')
    page.click('[data-create="item"] [data-commit]')

    # The write's own refetch re-renders; the item appears under its priority group.
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


def test_ideas_capture_flat_and_disclosure(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    ctx = context_factory()
    page = open_page(ctx, server, "#/ideas", '[data-screen="ideas"] .capture')

    # A title-only capture → a FLAT row with no disclosure.
    page.fill('[data-create="idea"] [data-input="title"]', "Dark mode only, skip the light theme")
    page.click('[data-create="idea"] [data-commit]')
    page.wait_for_selector('[data-ideas] div.list-row[data-idea-id]', timeout=WAIT_MS)
    flat = page.query_selector('[data-ideas] div.list-row[data-idea-id]')
    assert flat is not None
    flat_text = flat.text_content()
    assert flat_text is not None
    assert "Dark mode only, skip the light theme" in flat_text
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
    assert details is not None
    idea_title = details.query_selector(".it")
    assert idea_title is not None
    idea_title_text = idea_title.text_content()
    assert idea_title_text is not None
    assert "One-question onboarding" in idea_title_text
    # The body is present in the DOM but the disclosure starts closed.
    idea_body = details.query_selector(".disclosure-body")
    assert idea_body is not None
    idea_body_text = idea_body.text_content()
    assert idea_body_text is not None
    assert "Ask one thing that matters" in idea_body_text
    assert details.get_attribute("open") is None

    # Expanding the chevron opens the disclosure.
    page.click('[data-ideas] details.disclosure--idea > summary')
    page.wait_for_selector('[data-ideas] details.disclosure--idea[open]', timeout=WAIT_MS)

    # The title-only idea is still flat below it.
    assert page.query_selector('[data-ideas] div.list-row[data-idea-id]') is not None
