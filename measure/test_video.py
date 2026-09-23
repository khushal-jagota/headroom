"""Why does pressing Full re-transfer a video that never left the screen?"""
from __future__ import annotations
import sqlite3
from collections.abc import Callable
from playwright.sync_api import BrowserContext
from tests.e2e.harness import JsonObject, ServerHandle
from measure.test_baseline import _seed, _seed_thread, _open

WATCH = """
() => {
  window.__videoLog = [];
  const nodes = [...document.querySelectorAll('video')];
  nodes.forEach((v, i) => {
    v.dataset.videoStamp = 'v' + i;
    for (const name of ['loadstart', 'emptied', 'abort', 'suspend', 'stalled']) {
      v.addEventListener(name, () => window.__videoLog.push('v' + i + ':' + name));
    }
  });
  return nodes.length;
}
"""
READ = """
() => [window.__videoLog.slice(0, 40),
       [...document.querySelectorAll('video')].map(v => v.dataset.videoStamp ?? 'NEW')]
"""


def test_video(server: ServerHandle, context_factory: Callable[[], BrowserContext],
               cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Video probe")["id"]
    paths = _seed(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)

    page = context_factory().new_page()
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url)
            if "walkthrough.mp4" in r.url else None)
    _open(page, server, ticket_id)
    page.wait_for_timeout(5000)
    count = page.evaluate(WATCH)
    print(f"\nvideo elements on screen after the open: {count}")
    print(f"video requests during the open: {len(requests)}")

    requests.clear()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(5000)
    log, stamps = page.evaluate(READ)
    print(f"\nafter pressing Full:")
    print(f"  video requests   : {len(requests)}")
    print(f"  video elements   : {stamps}   ('NEW' means the node was rebuilt)")
    print(f"  media events     : {log}")
