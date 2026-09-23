"""Measurement only: count the file requests one conversation makes.

Not a product test. This reproduces the reported duplication in isolation and attributes
each request to the thing that asked for it. Run it with the e2e fixtures:

    .venv/bin/python -m pytest measure/test_measure_duplicate_fetches.py -s
"""

from __future__ import annotations

import base64
import collections
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Request, Response
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAW0lEQVR4nO3PQQ0AIBDAsAP/"
    "nuGNAvZoFSzZOjNnyNi1dwfgUQCeBOBJAI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJ"
    "AI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJAN4A2icCftL8UqQAAAAASUVORK5CYII="
)


def _files_root(server: ServerHandle, ticket_id: str) -> Path:
    return server.db_path.parent / "files" / "tickets" / ticket_id


def _seed_files(server: ServerHandle, ticket_id: str) -> list[str]:
    """23 distinct managed files, the mix a real artifact-heavy thread carries."""
    root = _files_root(server, ticket_id)
    (root / "images").mkdir(parents=True)
    (root / "notes").mkdir(parents=True)
    (root / "video").mkdir(parents=True)
    paths: list[str] = []

    for index in range(12):
        path = f"images/shot-{index}.png"
        (root / path).write_bytes(PNG)
        paths.append(path)

    # One big video, the 8.5 MB item from the report.
    (root / "video/walkthrough.mp4").write_bytes(os.urandom(8_500_000))
    paths.append("video/walkthrough.mp4")

    for index in range(4):
        path = f"notes/note-{index}.md"
        (root / path).write_text(f"# Note {index}\n\nSome body text.\n", encoding="utf-8")
        paths.append(path)

    for index in range(3):
        path = f"report-{index}.html"
        (root / path).write_text(f"<h1>Report {index}</h1>", encoding="utf-8")
        paths.append(path)

    for index in range(3):
        path = f"notes/data-{index}.md"
        (root / path).write_text(f"# Data {index}\n\n| a | b |\n| - | - |\n", encoding="utf-8")
        paths.append(path)

    assert len(paths) == 23
    return paths


def _message_text(ticket_id: str, paths: list[str]) -> str:
    lines = []
    for path in paths:
        label = path.rsplit("/", 1)[-1]
        href = f"/files/tickets/{ticket_id}/{path}"
        prefix = "!" if path.endswith(".png") else ""
        lines.append(f"{prefix}[{label}]({href})")
    return "Here is the work.\n\n" + "\n\n".join(lines)


def _seed_conversation(
    conn: sqlite3.Connection, conversation_id: str, ticket_id: str, paths: list[str]
) -> None:
    """Eight turns, shaped like a worker thread.

    Each turn: the owner prompts, a tool call runs, the agent says something along the
    way, then it addresses the owner. Focus draws the prompts and the addressed
    messages. Full draws the agent chatter too. The artifacts ride in one addressed
    message, as a worker's proposal carries them.
    """
    owner = {"kind": "owner", "id": "owner"}
    ticket_principal = {"kind": "ticket", "id": ticket_id}
    events: list[tuple[int, str, dict[str, object]]] = []
    sequence = 0
    for turn in range(8):
        sequence += 1
        events.append(
            (sequence, "prompt", {"text": f"Step {turn}", "sender_label": "owner",
                                  "mode": "queue", "sender": owner,
                                  "recipient": ticket_principal})
        )
        sequence += 1
        events.append((sequence, "tool_call_started", {
            "tool_call_id": f"c{turn}", "title": f"Read file {turn}",
            "tool_kind": "read", "detail": None}))
        sequence += 1
        events.append((sequence, "tool_call_finished", {
            "tool_call_id": f"c{turn}", "tool_call_status": "completed", "detail": "done"}))
        sequence += 1
        # Work chatter. Full draws this; Focus does not. It carries two pictures.
        chatter = "Working.\n\n" + _message_text(ticket_id, paths[turn % 6: turn % 6 + 2])
        events.append((sequence, "agent_message", {"text": chatter}))
        sequence += 1
        body = (
            _message_text(ticket_id, paths) if turn == 3 else f"Step {turn} is done."
        )
        events.append((sequence, "message_to_owner", {
            "text": body, "sender_label": "Coding worker",
            "sender": ticket_principal, "recipient": owner}))
        sequence += 1
        events.append((sequence, "turn_ended", {"ending": "completed", "error_summary": None}))

    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, latest_sequence, created_at) VALUES (?, 'claude', 'opus', '/tmp/w', 'full', ?, ?)",
        (conversation_id, sequence, 1_700_000_000),
    )
    conn.executemany(
        "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (conversation_id, seq, kind, json.dumps(payload), 1_700_000_000 + seq)
            for seq, kind, payload in events
        ],
    )
    conn.execute(
        "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
        (conversation_id, ticket_id),
    )
    conn.execute("UPDATE tickets SET conversation_id = ? WHERE id = ?", (conversation_id, ticket_id))


class Recorder:
    def __init__(self, page: Page) -> None:
        self.requests: list[tuple[str, str]] = []
        self.responses: list[tuple[str, int, str, str]] = []
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, request: Request) -> None:
        self.requests.append((request.url, request.resource_type))

    def _on_response(self, response: Response) -> None:
        headers = response.all_headers()
        self.responses.append(
            (
                response.url,
                response.status,
                headers.get("content-length", "?"),
                ";".join(
                    f"{name}={headers[name]}"
                    for name in ("cache-control", "etag", "last-modified", "content-range")
                    if name in headers
                ),
            )
        )

    def file_requests(self) -> list[str]:
        return [url for url, _ in self.requests if "/files/tickets/" in url]

    def reset(self) -> None:
        self.requests.clear()
        self.responses.clear()


def _report(title: str, recorder: Recorder) -> None:
    files = recorder.file_requests()
    counts = collections.Counter(url.split("/files/tickets/")[-1] for url in files)
    total_bytes = sum(
        int(length)
        for url, status, length, _ in recorder.responses
        if "/files/tickets/" in url and length.isdigit() and status < 400
    )
    print(f"\n===== {title} =====")
    print(f"file requests: {len(files)}   distinct files: {len(counts)}   bytes: {total_bytes:,}")
    repeats = {path: n for path, n in counts.items() if n > 1}
    print(f"files fetched more than once: {len(repeats)}")
    for path, n in sorted(repeats.items(), key=lambda kv: -kv[1]):
        print(f"   {n}x  {path}")
    seen: set[str] = set()
    for url, status, length, cache in recorder.responses:
        if "/files/tickets/" not in url or url in seen:
            continue
        seen.add(url)
        print(f"   [{status}] len={length} cache[{cache or 'NONE'}] {url.split('/files/tickets/')[-1]}")
        if len(seen) >= 3:
            break


def test_measure(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id: str = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Overfetch measurement"
    )["id"]
    paths = _seed_files(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_conversation(conn, "conv_overfetch", ticket_id, paths)

    # --- Pass one: open, stay in Focus (the default lens), then switch to Full.
    context = context_factory()
    page = context.new_page()
    recorder = Recorder(page)
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(4000)
    _report("OPEN in Focus", recorder)

    recorder.reset()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(4000)
    _report("Focus -> Full (switch only)", recorder)

    # --- Pass two: a second toggle back and forth, on the same page.
    recorder.reset()
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(2000)
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(3000)
    _report("Full -> Focus -> Full (same page, second time)", recorder)

    # --- Pass three: a fresh page that lands straight in Full.
    context2 = context_factory()
    page2 = context2.new_page()
    recorder2 = Recorder(page2)
    page2.goto(server.base + f"/#/workspace/{ticket_id}")
    page2.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page2.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page2.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page2.locator("[data-conversation-lens-toggle]").click()
    page2.wait_for_timeout(5000)
    _report("OPEN then Full, fresh context", recorder2)
