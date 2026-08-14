"""Browser proof that open screens follow the server, and that composing survives it.

The server says only "something changed"; the browser answers by refetching what it is
showing. These two tests hold that behaviour to what a person actually experiences: a
change made elsewhere lands on the open screen without a reload, and a change that lands
while someone is typing leaves the typing — the caret, the text, and the place on the
page — exactly where it was.

Waits are on the browser's own counters (``window.__plannerDebug``) plus the resulting
DOM, never on a sleep.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000

RECAP_SEED = "Recap as it stood before anyone started typing."
FIRST_HALF = " Composed the first half,"
SECOND_HALF = " and the second half after the change."

# Find the surface the composer scrolls inside, centre the composer in it, and keep both
# for later: the same editor node must still hold the caret at the end, and its surface
# must still be sitting at the same place.
CAPTURE_COMPOSER = """selector => {
    const editor = document.querySelector(selector);
    editor.scrollIntoView({ block: "center" });
    let node = editor.parentElement;
    while (node) {
        const style = getComputedStyle(node);
        const scrolls = style.overflowY === "auto" || style.overflowY === "scroll";
        if (scrolls && node.scrollHeight > node.clientHeight + 1) break;
        node = node.parentElement;
    }
    const scroller = node || document.scrollingElement;
    window.__composer = { editor, scroller };
    return scroller.scrollTop;
}"""

# Focus without scrolling (focus() would otherwise move the page), then put the caret at
# the end of what is already written — where a person would carry on.
FOCUS_AT_END = """() => {
    const editor = window.__composer.editor;
    editor.focus({ preventScroll: true });
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
}"""


def _set_recap(server: ServerHandle, ticket_id: str, recap: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET recap = ? WHERE id = ?", (recap, ticket_id))


def _flushes(page: Page) -> int:
    return int(page.evaluate("() => window.__plannerDebug.flushes"))


def _wait_for_flush(page: Page, previous: int) -> None:
    page.wait_for_function(
        "previous => window.__plannerDebug.flushes > previous",
        arg=previous,
        timeout=WAIT_MS,
    )


def _mark_page(page: Page) -> None:
    """A value that only survives while this document does — a reload wipes it."""
    page.evaluate("() => { window.__documentMark = 'same document'; }")


def _assert_same_document(page: Page) -> None:
    assert page.evaluate("() => window.__documentMark") == "same document"


def test_a_change_reaches_the_open_board_without_a_reload(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Board card before the change",
    )["id"]

    page = open_page(context_factory(), server, "#/workspace", 'section[data-screen="workspace"]')
    page.click('[data-workspace-view="attention"]')
    page.wait_for_selector('[data-workspace-view="all"]', timeout=WAIT_MS)
    card = f'[data-card][data-ticket-id="{ticket_id}"]'
    page.wait_for_selector(card, timeout=WAIT_MS)
    assert "Board card before the change" in page.inner_text(card)

    _mark_page(page)
    url_before = page.url
    flushes = _flushes(page)

    api.direct_patch(
        server, f"/api/tickets/{ticket_id}", {"title": "Board card after the change"}
    )

    _wait_for_flush(page, flushes)
    page.wait_for_function(
        "selector => document.querySelector(selector)"
        "?.textContent.includes('Board card after the change')",
        arg=card,
        timeout=WAIT_MS,
    )

    # The card changed inside the page that was already open: same document, same URL.
    _assert_same_document(page)
    assert page.url == url_before
    assert "Board card before the change" not in page.inner_text(card)


def test_composing_survives_a_change_to_the_same_ticket(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Composer ticket before the change",
    )["id"]
    _set_recap(server, ticket_id, RECAP_SEED)

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    # A short window makes the ticket's own column scroll, so "the page did not jump"
    # is a claim with something behind it.
    page.set_viewport_size({"width": 1280, "height": 400})

    composer = "[data-recap] [data-markdown-inline-edit]"
    page.locator(composer).wait_for(state="visible", timeout=WAIT_MS)
    scroll_top = page.evaluate(CAPTURE_COMPOSER, composer)
    assert scroll_top > 0, scroll_top

    page.evaluate(FOCUS_AT_END)
    page.keyboard.type(FIRST_HALF)
    page.wait_for_function(
        "text => window.__composer.editor.textContent.includes(text)",
        arg=FIRST_HALF.strip(),
        timeout=WAIT_MS,
    )

    flushes = _flushes(page)
    _mark_page(page)
    api.direct_patch(
        server, f"/api/tickets/{ticket_id}", {"title": "Composer ticket after the change"}
    )

    # The change reaches this very screen mid-composition: the title on the page behind
    # the composer is rewritten from the server's answer.
    _wait_for_flush(page, flushes)
    page.locator(".ticket-title", has_text="Composer ticket after the change").wait_for(
        state="visible", timeout=WAIT_MS
    )

    page.keyboard.type(SECOND_HALF)

    state = page.evaluate(
        """() => {
            const { editor, scroller } = window.__composer;
            return {
                focused: document.activeElement === editor,
                stillAttached: editor.isConnected,
                text: editor.textContent,
                scrollTop: scroller.scrollTop
            };
        }"""
    )
    assert state["stillAttached"] is True
    assert state["focused"] is True
    assert state["text"] == RECAP_SEED + FIRST_HALF + SECOND_HALF
    assert state["scrollTop"] == scroll_top
    _assert_same_document(page)
