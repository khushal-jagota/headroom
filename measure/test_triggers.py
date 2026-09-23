"""Which action makes a file be fetched again?

Two instruments, because they answer different questions:

  * ``performance.getEntriesByType("resource")`` counts every time the page ASKED for a
    file, cache hits included. A cache hit has ``transferSize`` 0.
  * Playwright's request events count what reached the network stack.

A remount that the browser cache absorbs still shows up in the first and not the second.
"""

from __future__ import annotations

import collections
import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_measure_duplicate_fetches import _seed_files, _seed_conversation

PERF = """
() => performance.getEntriesByType('resource')
  .filter(e => e.name.includes('/files/tickets/'))
  .map(e => [e.name.split('/files/tickets/')[1], e.transferSize, e.encodedBodySize])
"""


class Probe:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.network: list[str] = []
        page.on("request", self._on)
        self.mark = 0

    def _on(self, request) -> None:  # noqa: ANN001
        if "/files/tickets/" in request.url:
            self.network.append(request.url.split("/files/tickets/")[1])

    def report(self, title: str) -> None:
        entries = self.page.evaluate(PERF)
        asked = collections.Counter(name for name, _, _ in entries)
        transferred = sum(size for _, size, _ in entries)
        net = collections.Counter(self.network)
        repeats = {k: v for k, v in asked.items() if v > 1}
        lens = self.page.get_attribute("[data-conversation-lens-toggle]", "data-conversation-lens")
        state = self.page.get_attribute("[data-conversation-pane]", "data-conversation-state")
        print(f"\n----- {title} -----")
        print(f"  lens={lens} state={state} rows={self.page.locator('[data-conversation-row]').count()}"
              f" previews={self.page.locator('[data-file-preview]').count()}"
              f" imgs={self.page.locator('.file-preview-image').count()}")
        print(f"  asked for a file      : {sum(asked.values())}  (distinct {len(asked)})")
        print(f"  reached the network   : {sum(net.values())}")
        print(f"  bytes transferred     : {transferred:,}")
        print(f"  files asked for twice+: {len(repeats)}")
        for name, count in sorted(repeats.items(), key=lambda kv: -kv[1])[:6]:
            print(f"      {count}x  {name}   (network {net.get(name, 0)}x)")

    def clear(self) -> None:
        self.page.evaluate("() => performance.clearResourceTimings()")
        self.network.clear()


def test_triggers(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id: str = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Trigger hunt"
    )["id"]
    paths = _seed_files(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_conversation(conn, "conv_trigger", ticket_id, paths)

    page = context_factory().new_page()
    # Count every ask, including the ones the browser cache would answer on its own.
    # Interception sits in front of the cache, which is also the state the server puts a
    # freshly written artifact in: no Cache-Control, and no 304 when asked.
    page.route("**/files/tickets/**", lambda route: route.continue_())
    probe = Probe(page)
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(4000)
    probe.report("1. open the conversation, Focus")

    probe.clear()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(3000)
    probe.report("2. Focus -> Full")

    probe.clear()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(1500)
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(2500)
    probe.report("3. Full -> Focus -> Full again")

    probe.clear()
    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.wait_for_timeout(3000)
    probe.report("4. put it away, open it again")

    probe.clear()
    page.locator("[data-conversation-expand]").click()
    page.wait_for_timeout(2500)
    probe.report("5. card -> full height")

    probe.clear()
    turns = page.locator("[data-conversation-turn-settled-head]")
    if turns.count():
        turns.first.click()
        page.wait_for_timeout(1500)
        turns.first.click()
        page.wait_for_timeout(1500)
        turns.first.click()
        page.wait_for_timeout(2000)
    probe.report("6. open a settled turn's fold, shut it, open it again")

    # A write anywhere in the system: the change signal invalidates every query.
    probe.clear()
    for index in range(3):
        cli(server, "ticket", "create", "--worker-type", "coding", "--title", f"Noise {index}")
        page.wait_for_timeout(1200)
    page.wait_for_timeout(2500)
    probe.report("7. three unrelated writes land (change signal)")
