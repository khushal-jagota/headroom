"""One reader, five phases, counted per phase. The condition comes from PANELS_CONDITION.

Phases, in order, on one browser context:
  cold open   - a fresh context, nothing in any cache, conversation opened in Focus
  press Full  - the Full-only rows arrive
  press play  - the first video is played for two seconds
  seek        - the same video is moved to 70% and played again
  warm reopen - the page is reloaded in the same context, so every cache is warm

Bytes are the sum of content-length on responses that carried one. A 304 carries none,
which is the point: it is a request that moved no body.
"""
from __future__ import annotations

import collections
import os
import sqlite3
import time
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_baseline import _seed, _seed_thread, _open

CONDITION = os.environ.get("PANELS_CONDITION", "baseline")


class Phase:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.hits: list[tuple[str, int, int]] = []
        self.last = time.monotonic()
        page.on("request", self._request)
        page.on("response", self._response)

    def _request(self, request) -> None:  # noqa: ANN001
        if "/files/tickets/" in request.url:
            self.hits.append((request.url.split("/files/tickets/")[1].split("/", 1)[1], 0, 0))
            self.last = time.monotonic()

    def _response(self, response) -> None:  # noqa: ANN001
        if "/files/tickets/" not in response.url:
            return
        name = response.url.split("/files/tickets/")[1].split("/", 1)[1]
        try:
            size = int(response.all_headers().get("content-length", "0") or 0)
        except ValueError:
            size = 0
        self.hits.append((name, size, response.status))
        self.last = time.monotonic()

    def settle(self, quiet: float = 5.0, budget: float = 90.0) -> None:
        end = time.monotonic() + budget
        self.last = time.monotonic()
        while time.monotonic() < end:
            self.page.wait_for_timeout(500)
            if time.monotonic() - self.last >= quiet:
                return

    def take(self, label: str) -> None:
        requests = [name for name, _, status in self.hits if status == 0]
        bodies = [(name, size, status) for name, size, status in self.hits if status]
        total = sum(size for _, size, status in bodies if status != 304)
        not_modified = sum(1 for _, _, status in bodies if status == 304)
        counts = collections.Counter(requests)
        repeats = {k: v for k, v in counts.items() if v > 1}
        print(f"  {label:<12} requests {len(requests):>4}   distinct {len(counts):>3}   "
              f"304s {not_modified:>4}   bytes {total:>12,}   repeated files {len(repeats)}")
        if repeats:
            top = sorted(repeats.items(), key=lambda kv: -kv[1])[:3]
            print(f"               repeated: " + ", ".join(f"{v}x {k}" for k, v in top))
        self.hits.clear()
        self.last = time.monotonic()


def test_matrix(server: ServerHandle, context_factory: Callable[[], BrowserContext],
                cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Matrix")["id"]
    paths = _seed(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)

    context = context_factory()
    page = context.new_page()
    phase = Phase(page)
    print(f"\n========== condition: {CONDITION} ==========")

    _open(page, server, ticket_id)
    phase.settle()
    phase.take("cold open")

    page.locator("[data-conversation-lens-toggle]").click()
    phase.settle()
    phase.take("press Full")

    video = page.locator("video").first
    video.evaluate("v => { v.muted = true; return v.play(); }")
    page.wait_for_timeout(2500)
    phase.settle(quiet=4.0, budget=45)
    played = video.evaluate("v => [v.currentTime, v.duration, v.readyState]")
    phase.take("press play")
    print(f"               played to {played[0]:.2f}s of {played[1]}s, readyState {played[2]}")

    video.evaluate("v => { v.currentTime = v.duration * 0.7; return v.play(); }")
    page.wait_for_timeout(2500)
    phase.settle(quiet=4.0, budget=45)
    sought = video.evaluate("v => [v.currentTime, v.readyState]")
    phase.take("seek")
    print(f"               now at {sought[0]:.2f}s, readyState {sought[1]}")

    page.reload()
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    phase.settle()
    phase.take("warm reopen")
