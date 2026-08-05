"""The Ticket page's one column, measured in a real browser.

The page is one column between two fixed gutters, and the header, the body and the
conversation are all in it. Three things are asserted here, because three things can break
it. The column is measured against the pane it is drawn in and not the window, so the same
route gives 1344px standing alone in a 1728px window and 832px inside that window's
Workspace right pane. The three parts agree on one width and one left edge, because the
document reserves its scrollbar and the conversation does not. And below the point where
the gutters fit, everything is exactly what it was before this rule existed, because the
narrow layout belongs to another Ticket.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import JsonObject, ServerHandle

PANE = "[data-conversation-pane]"
INPUT = "[data-conversation-input]"
EXPAND = "[data-conversation-expand]"

# The gutter each side of the column, and the pane width at which the gutters start to fit.
GUTTER = 192
CROSSOVER = 1152

GEOMETRY = """
() => {
  const box = (selector) => {
    const element = document.querySelector(selector);
    if (element === null) return null;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      width: Math.round(rect.width),
      left: Math.round(rect.left),
      padLeft: style.paddingLeft,
      padRight: style.paddingRight
    };
  };
  const doc = document.querySelector('.ticket-doc');
  return {
    pane: box('.ticket-screen'),
    head: box('.ticket-head'),
    col: box('.ticket-col'),
    conversation: box('.ticket-conversation-column'),
    rail: box('.board-workspace-left'),
    docClientWidth: doc === null ? null : doc.clientWidth,
    docOverflows: doc === null ? null : doc.scrollWidth > doc.clientWidth
  };
}
"""


def _a_ticket_with_a_conversation(
    server: ServerHandle, cli: Callable[..., JsonObject], title: str
) -> str:
    """A Ticket whose conversation exists, so the layer has its states to be measured in."""
    created = cli(server, "ticket", "create", "--worker-type", "coding", "--title", title)
    ticket_id: str = created["id"]
    started = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/conversation/send",
        json={
            "conversation_id": None,
            "content": [{"piece": "text", "text": "the column is measured against the pane"}],
            "sender_label": "owner",
        },
        timeout=10.0,
    )
    assert started.status_code == 200, started.text
    return ticket_id


def _the_page_at(
    server: ServerHandle,
    context: BrowserContext,
    open_page: Callable[..., Page],
    ticket_id: str,
    route: str,
    width: int,
) -> Page:
    page = open_page(
        context,
        server,
        route,
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    page.set_viewport_size({"width": width, "height": 1000})
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)
    page.wait_for_selector(f"{INPUT}:not([disabled])", timeout=WAIT_MS)
    return page


def _assert_one_fluid_column(geometry: JsonObject) -> None:
    """Header, body and conversation are one column of pane less a gutter each side."""
    pane = geometry["pane"]["width"]
    assert pane >= CROSSOVER, pane
    expected = pane - 2 * GUTTER
    for part in ("head", "col", "conversation"):
        assert geometry[part]["width"] == expected, (part, geometry[part], expected)
        assert geometry[part]["left"] == GUTTER + geometry["pane"]["left"], (
            part,
            geometry[part],
        )
        # The design's column inset, the same one the conversation already had.
        assert geometry[part]["padLeft"] == "48px", (part, geometry[part])
        assert geometry[part]["padRight"] == "48px", (part, geometry[part])
    assert geometry["docOverflows"] is False


def test_the_column_is_the_pane_less_a_gutter_each_side(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = _a_ticket_with_a_conversation(server, cli, "One fluid column, both windows")

    # Standing alone the pane is the window, so the column is the window less the gutters.
    for width in (1728, 2560):
        page = _the_page_at(
            server, context_factory(), open_page, ticket_id, f"#/ticket/{ticket_id}", width
        )
        geometry = page.evaluate(GEOMETRY)
        assert geometry["pane"]["width"] == width, geometry["pane"]
        _assert_one_fluid_column(geometry)
        page.close()

    # In the Workspace the pane is the right side of the split, and the rail is untouched.
    for width, expected_column in ((1728, 832), (2560, 1664)):
        page = _the_page_at(
            server, context_factory(), open_page, ticket_id, f"#/workspace/{ticket_id}", width
        )
        geometry = page.evaluate(GEOMETRY)
        assert geometry["rail"]["width"] == 512, geometry["rail"]
        assert geometry["pane"]["width"] == width - 512, geometry["pane"]
        _assert_one_fluid_column(geometry)
        assert geometry["head"]["width"] == expected_column, geometry["head"]
        page.close()


def test_the_column_does_not_move_when_the_conversation_opens(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Rest, peeked and opened are heights. None of them is a width."""
    ticket_id = _a_ticket_with_a_conversation(server, cli, "The states keep the column")
    page = _the_page_at(
        server, context_factory(), open_page, ticket_id, f"#/workspace/{ticket_id}", 1728
    )
    at_rest = page.evaluate(GEOMETRY)
    _assert_one_fluid_column(at_rest)

    page.click(INPUT, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)
    peeked = page.evaluate(GEOMETRY)
    _assert_one_fluid_column(peeked)

    page.click(EXPAND, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)
    opened = page.evaluate(GEOMETRY)
    assert opened["conversation"]["width"] == at_rest["conversation"]["width"]
    assert opened["conversation"]["left"] == at_rest["conversation"]["left"]
    assert opened["conversation"]["padLeft"] == "48px", opened["conversation"]


def test_a_narrow_pane_keeps_the_layout_it_had(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Below the crossover the newer mobile gutter system owns the page insets."""
    ticket_id = _a_ticket_with_a_conversation(server, cli, "The narrow page is untouched")
    page = _the_page_at(
        server, context_factory(), open_page, ticket_id, f"#/ticket/{ticket_id}", 390
    )
    geometry = page.evaluate(GEOMETRY)

    # The document and the conversation fill what they are drawn in, as they did before.
    assert geometry["head"]["width"] == geometry["docClientWidth"], geometry
    assert geometry["col"]["width"] == geometry["docClientWidth"], geometry
    assert geometry["conversation"]["width"] == geometry["pane"]["width"], geometry
    # The mobile gutter is shared by the masthead, document column, and conversation.
    assert geometry["head"]["padLeft"] == "16px", geometry["head"]
    assert geometry["col"]["padLeft"] == "16px", geometry["col"]
    assert geometry["conversation"]["padLeft"] == "16px", geometry["conversation"]
    assert geometry["docOverflows"] is False
