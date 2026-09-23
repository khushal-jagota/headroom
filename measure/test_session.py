"""One reader's whole session, counted once. No windows, so no bytes land in the wrong one."""
from __future__ import annotations
import collections, sqlite3, time
from collections.abc import Callable
from playwright.sync_api import BrowserContext
from tests.e2e.harness import JsonObject, ServerHandle
from measure.test_baseline import _seed, _seed_thread, _open


def test_session(server: ServerHandle, context_factory: Callable[[], BrowserContext],
                 cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Session")["id"]
    paths = _seed(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)

    page = context_factory().new_page()
    fetches: list[str] = []
    total = {"bytes": 0, "last": time.monotonic()}

    def on_request(request) -> None:  # noqa: ANN001
        if "/files/tickets/" in request.url:
            fetches.append(request.url.split("/files/tickets/")[1].split("/", 1)[1])
            total["last"] = time.monotonic()

    def on_response(response) -> None:  # noqa: ANN001
        if "/files/tickets/" in response.url:
            try:
                total["bytes"] += int(response.all_headers().get("content-length", "0") or 0)
            except ValueError:
                pass
            total["last"] = time.monotonic()

    page.on("request", on_request)
    page.on("response", on_response)

    _open(page, server, ticket_id)
    page.wait_for_timeout(3000)
    page.locator("[data-conversation-lens-toggle]").click()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        if time.monotonic() - total["last"] >= 10:
            break

    counts = collections.Counter(fetches)
    distinct_bytes = 12 * 161 + 8_431_879 + 4 * 17 + 3 * 17 + 3 * 30
    print("\n===== one reader: open the conversation, then press Full =====")
    print(f"  distinct files on screen : {len(counts)}")
    print(f"  previews mounted         : {page.locator('[data-file-preview]').count()}")
    print(f"  network fetches          : {sum(counts.values())}")
    print(f"  bytes off the wire       : {total['bytes']:,}")
    print(f"  distinct content is about: {distinct_bytes:,} bytes")
    print(f"  files fetched more than once:")
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if count > 1:
            print(f"      {count}x  {name}")
