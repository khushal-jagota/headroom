"""The dev conversation pane loads real markup from the real server.

Two assertions a browser must make and node tests cannot: the route on a running
``panels serve`` renders the distinct empty state with the backend cards, and a
conversation created through the HTTP API reloads into the pane surface. Real agent
turns are deliberately absent here — they belong to the real-CLI exercises and the
owner's dogfooding, not a repeatable gate.
"""

from __future__ import annotations

import httpx

WAIT_MS = 10_000
BACKEND_CARD_WAIT_MS = 30_000  # backend cards probe real CLIs with subprocess calls


def test_the_dev_route_renders_the_empty_state_and_backend_cards(
    server, context_factory, open_page
) -> None:
    page = open_page(
        context_factory(), server, "#/dev/conversation", "[data-conversation2-route]"
    )
    # The empty state is a real surface, visibly distinct from a broken blank screen.
    page.wait_for_selector("[data-conversation2-new]", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation2-new-id]", timeout=WAIT_MS)
    for backend_key in ("hermes", "codex", "claude"):
        page.wait_for_selector(
            f'[data-conversation2-backend="{backend_key}"]', timeout=BACKEND_CARD_WAIT_MS
        )


def test_a_started_conversation_reloads_into_the_pane_surface(
    server, context_factory, open_page
) -> None:
    created = httpx.post(
        f"{server.base}/api/conversation2/conversations",
        json={"conversation_id": "e2e-dev-pane", "backend_key": "codex"},
        timeout=10.0,
    )
    assert created.status_code == 201, created.text

    page = open_page(
        context_factory(),
        server,
        "#/dev/conversation?id=e2e-dev-pane",
        "[data-conversation2-pane]",
    )
    page.wait_for_selector("[data-conversation2-thread]", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation2-workspace]", timeout=WAIT_MS)
