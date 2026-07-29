"""What the Chief's panel opens on before there is a conversation.

The rest of this file's browser coverage was retired when the verification tiers were
right-sized; what is kept is the one thing that had no proof before — a panel with no
conversation showing what a first message would actually run on.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000
MODEL_PICKER = "[data-conversation-picker-model]"


def test_the_chief_panel_opens_on_the_backend_it_is_configured_on(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """A panel with no conversation shows what a first message would actually run on.

    The Chief is moved off the backend it ships on, and the rail follows: it says what
    the Chief is configured on rather than what a backend does when nobody says.
    """
    moved = httpx.put(
        f"{server.base}/api/workers/chief-of-staff/launch-defaults",
        json={
            "employee_backend": "claude",
            "employee_launch_model": "sonnet",
            "employee_launch_reasoning_effort": None,
        },
        timeout=10.0,
    )
    assert moved.status_code < 300, moved.text

    page = open_page(
        context_factory(),
        server,
        "#/agents/chief-of-staff",
        'section[data-screen="chief"] [data-conversation-input]',
    )

    # The rail is drawn down the side of the model picker's panel, which is where a
    # person goes to see or change what the next message would start.
    page.click(f"{MODEL_PICKER} [data-conversation-picker-trigger]")
    page.wait_for_selector("[data-conversation-backend-showing]", timeout=WAIT_MS)
    assert page.inner_text("[data-conversation-backend-showing]") == "claude"
    # And the model beside it is the Chief's own, whether or not this machine has claude
    # installed to name it more prettily than the value itself.
    assert "sonnet" in page.inner_text(f"{MODEL_PICKER} .c2-pick-face").lower()
