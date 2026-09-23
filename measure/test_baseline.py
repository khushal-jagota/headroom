"""The baseline this Ticket must improve on.

One conversation, 23 distinct managed files, read the way a reader reads it: open it,
switch to Full, come back to it fresh. Counted three ways, because they are three
different facts:

  * asked    - every time the page asked for a file, cache hits included
  * network  - every ask that reached the server
  * bytes    - what actually came down the wire
"""

from __future__ import annotations

import base64
import collections
import json
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAW0lEQVR4nO3PQQ0AIBDAsAP/"
    "nuGNAvZoFSzZOjNnyNi1dwfgUQCeBOBJAI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJ"
    "AI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJAN4A2icCftL8UqQAAAAASUVORK5CYII="
)

VIDEO = Path(__file__).parent / "assets" / "walkthrough.mp4"


def _ensure_video() -> None:
    """An 8.4 MB playable mp4, built once. Random pixels, so it does not compress away."""
    if VIDEO.exists():
        return
    VIDEO.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "nullsrc=size=640x360:rate=25:duration=2.3,geq=random(1)*255:128:128",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12",
         "-movflags", "+faststart", str(VIDEO)],
        check=True, capture_output=True,
    )
PERF = """
() => performance.getEntriesByType('resource')
  .filter(e => e.name.includes('/files/tickets/'))
  .map(e => [e.name.split('/files/tickets/')[1].split('/', 1)[1], e.transferSize])
"""


def _seed(server: ServerHandle, ticket_id: str) -> list[str]:
    _ensure_video()
    root = server.db_path.parent / "files" / "tickets" / ticket_id
    (root / "images").mkdir(parents=True)
    (root / "notes").mkdir(parents=True)
    (root / "video").mkdir(parents=True)
    paths = []
    for index in range(12):
        (root / f"images/shot-{index}.png").write_bytes(PNG)
        paths.append(f"images/shot-{index}.png")
    shutil.copy(VIDEO, root / "video/walkthrough.mp4")
    paths.append("video/walkthrough.mp4")
    for index in range(4):
        (root / f"notes/note-{index}.md").write_text(f"# Note {index}\n\nBody.\n")
        paths.append(f"notes/note-{index}.md")
    for index in range(3):
        (root / f"report-{index}.html").write_text(f"<h1>Report {index}</h1>")
        paths.append(f"report-{index}.html")
    for index in range(3):
        (root / f"notes/data-{index}.md").write_text(f"# Data {index}\n\n| a | b |\n| - | - |\n")
        paths.append(f"notes/data-{index}.md")
    assert len(paths) == 23
    return paths


def _links(ticket_id: str, paths: list[str]) -> str:
    out = []
    for path in paths:
        mark = "!" if path.endswith(".png") else ""
        out.append(f"{mark}[{path.rsplit('/', 1)[-1]}](/files/tickets/{ticket_id}/{path})")
    return "\n\n".join(out)


def _seed_thread(conn: sqlite3.Connection, ticket_id: str, paths: list[str]) -> None:
    owner = {"kind": "owner", "id": "owner"}
    me = {"kind": "ticket", "id": ticket_id}
    events, seq = [], 0
    for turn in range(8):
        seq += 1
        events.append((seq, "prompt", {"text": f"Step {turn}", "sender_label": "owner",
                                       "mode": "queue", "sender": owner, "recipient": me}))
        seq += 1
        events.append((seq, "tool_call_started", {"tool_call_id": f"c{turn}",
                                                  "title": f"Read file {turn}",
                                                  "tool_kind": "read", "detail": None}))
        seq += 1
        events.append((seq, "tool_call_finished", {"tool_call_id": f"c{turn}",
                                                   "tool_call_status": "completed",
                                                   "detail": "done"}))
        seq += 1
        # Work chatter, drawn in Full only. It names two pictures it just made.
        events.append((seq, "agent_message",
                       {"text": "Working.\n\n" + _links(ticket_id, paths[turn % 6: turn % 6 + 2])}))
        seq += 1
        if turn == 3:
            body = "Here is the package.\n\n" + _links(ticket_id, paths)
        elif turn in (5, 7):
            # A worker links its artifacts again when it refers back to them.
            body = "As in the walkthrough.\n\n" + _links(
                ticket_id, ["video/walkthrough.mp4", "notes/note-0.md", "report-0.html"]
            )
        else:
            body = f"Step {turn} is done."
        events.append((seq, "message_to_owner", {"text": body, "sender_label": "Coding worker",
                                                 "sender": me, "recipient": owner}))
        seq += 1
        events.append((seq, "turn_ended", {"ending": "completed", "error_summary": None}))

    conn.execute("INSERT INTO conversations (conversation_id, backend_key, model, "
                 "workspace_folder, access, latest_sequence, created_at) "
                 "VALUES ('conv_base', 'claude', 'opus', '/tmp/w', 'full', ?, ?)",
                 (seq, 1_700_000_000))
    conn.executemany("INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
                     "created_at) VALUES ('conv_base', ?, ?, ?, ?)",
                     [(s, k, json.dumps(p), 1_700_000_000 + s) for s, k, p in events])
    conn.execute("INSERT INTO ticket_conversations (conversation_id, ticket_id) "
                 "VALUES ('conv_base', ?)", (ticket_id,))
    conn.execute(
        "UPDATE tickets SET conversation_id = 'conv_base' WHERE id = ?", (ticket_id,)
    )


class Tally:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.network: list[str] = []
        self.bytes = 0
        self.inflight = 0
        self.last_activity = 0.0
        page.on("request", self._request)
        page.on("response", self._response)

    def _request(self, request) -> None:  # noqa: ANN001
        if "/files/tickets/" in request.url:
            self.network.append(request.url.split("/files/tickets/")[1].split("/", 1)[1])
            self.last_activity = time.monotonic()

    def _response(self, response) -> None:  # noqa: ANN001
        if "/files/tickets/" in response.url:
            try:
                self.bytes += int(response.all_headers().get("content-length", "0") or 0)
            except ValueError:
                pass
            self.last_activity = time.monotonic()

    def settle(self, quiet_seconds: float = 4.0, budget: float = 60.0) -> None:
        """Wait until no file request has started or finished for `quiet_seconds`."""
        deadline = time.monotonic() + budget
        self.last_activity = time.monotonic()
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(500)
            if time.monotonic() - self.last_activity >= quiet_seconds:
                return

    def report(self, title: str) -> None:
        entries = self.page.evaluate(PERF)
        asked = collections.Counter(name for name, _ in entries)
        net = collections.Counter(self.network)
        repeats = {k: v for k, v in net.items() if v > 1}
        print(f"\n----- {title} -----")
        print(f"  previews on screen : {self.page.locator('[data-file-preview]').count()}")
        print(f"  asked for a file   : {sum(asked.values())}   distinct {len(asked)}")
        print(f"  reached the network: {sum(net.values())}   distinct {len(net)}")
        print(f"  bytes off the wire : {self.bytes:,}")
        print(f"  fetched more than once over the network: {len(repeats)}")
        for name, count in sorted(repeats.items(), key=lambda kv: -kv[1])[:8]:
            print(f"      {count}x  {name}")

    def clear(self) -> None:
        self.page.evaluate("() => performance.clearResourceTimings()")
        self.network.clear()
        self.bytes = 0
        self.last_activity = time.monotonic()


def _open(page: Page, server: ServerHandle, ticket_id: str) -> None:
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)


def test_baseline(
    server: ServerHandle, context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Baseline")["id"]
    paths = _seed(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)

    page = context_factory().new_page()
    tally = Tally(page)
    _open(page, server, ticket_id)
    tally.settle()
    tally.report("A. open the conversation (Focus, the default)")

    tally.clear()
    page.locator("[data-conversation-lens-toggle]").click()
    tally.settle()
    tally.report("B. press Full")

    tally.clear()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(2000)
    page.locator("[data-conversation-lens-toggle]").click()
    tally.settle()
    tally.report("C. Focus and back to Full, a second time")

    page2 = context_factory().new_page()
    tally2 = Tally(page2)
    _open(page2, server, ticket_id)
    page2.locator("[data-conversation-lens-toggle]").click()
    tally2.settle()
    tally2.report("D. a fresh reader, straight to Full")
