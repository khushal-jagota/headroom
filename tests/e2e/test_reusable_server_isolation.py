"""Proof that the ordinary e2e fixture resets without cross-test state leakage.

These checks are deliberately consecutive. The first test in each pair contaminates the
ordinary fixture; the next test observes the fixture boundary that every other e2e test
receives. Database-only state can reuse the process, while process-owned managed state and
conversation runtime require a fresh process.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import FAKE_NOW, JsonObject, ServerHandle

_ordinary_process_id: int | None = None
_ordinary_ticket_id: str | None = None
_conversation_process_id: int | None = None
_CONVERSATION_ID = "reusable-server-isolation"


def test_01_ordinary_server_state_is_contaminated(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    global _ordinary_process_id, _ordinary_ticket_id

    _ordinary_process_id = server.proc.pid
    _ordinary_ticket_id = str(
        cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            "must not reach the next test",
        )["id"]
    )
    changed_clock = httpx.post(
        server.base + "/api/test/set-now",
        json={"now": "2026-07-05T12:00:00"},
        timeout=10.0,
    )
    assert changed_clock.status_code == 200, changed_clock.text

    managed_file = server.db_path.parent / "files" / "tickets" / _ordinary_ticket_id / "note.md"
    managed_file.parent.mkdir(parents=True)
    managed_file.write_text("must not reach the next test", encoding="utf-8")

    changed_owner = httpx.put(
        server.base + "/api/workers/coding/stages/needs_success/default-ownership",
        json={"ownership_mode": "user"},
        timeout=10.0,
    )
    assert changed_owner.status_code == 200, changed_owner.text
    changed_skill = httpx.patch(
        server.base + "/api/skills/panels-worker",
        json={"description": "must not reach the next test"},
        timeout=10.0,
    )
    assert changed_skill.status_code == 200, changed_skill.text

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]")
    page.evaluate("() => localStorage.setItem('cross-test-state', 'must not leak')")


def test_02_process_owned_managed_state_gets_fresh_process_and_clean_state(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    assert _ordinary_process_id is not None
    assert _ordinary_ticket_id is not None
    assert server.proc.pid != _ordinary_process_id

    missing_ticket = httpx.get(
        f"{server.base}/api/tickets/{_ordinary_ticket_id}",
        timeout=10.0,
    )
    assert missing_ticket.status_code == 404, missing_ticket.text
    assert not (server.db_path.parent / "files").exists()

    worker = httpx.get(server.base + "/api/workers/coding", timeout=10.0)
    assert worker.status_code == 200, worker.text
    assert worker.json()["settings"]["stage_ownership_defaults"]["needs_success"] == "worker"
    skill = httpx.get(server.base + "/api/skills/panels-worker", timeout=10.0)
    assert skill.status_code == 200, skill.text
    assert skill.json()["description"] != "must not reach the next test"

    today = httpx.get(server.base + "/api/day/today", timeout=10.0)
    assert today.status_code == 200, today.text
    assert today.json()["id"] == "day_" + FAKE_NOW[:10]

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]")
    assert page.evaluate("() => localStorage.getItem('cross-test-state')") is None
    assert page.evaluate("() => window.__plannerDebug.sseOpens") == 1


def test_03_conversation_runtime_is_contaminated(server: ServerHandle) -> None:
    global _conversation_process_id

    _conversation_process_id = server.proc.pid
    created = httpx.post(
        server.base + "/api/conversation/conversations",
        json={
            "conversation_id": _CONVERSATION_ID,
            "backend_key": "codex",
            # Every conversation is started on a named model. A name is enough here:
            # what this file is about is whether the runtime is carried between tests,
            # and nothing in it ever spawns a backend.
            "model": "e2e-model",
        },
        timeout=10.0,
    )
    assert created.status_code == 201, created.text


def test_04_conversation_state_gets_a_fresh_process(server: ServerHandle) -> None:
    assert _conversation_process_id is not None
    assert server.proc.pid != _conversation_process_id

    missing_conversation = httpx.get(
        f"{server.base}/api/conversation/conversations/{_CONVERSATION_ID}",
        timeout=10.0,
    )
    assert missing_conversation.status_code == 404, missing_conversation.text

    # The old id is not hiding in the replacement process's in-memory runtime either.
    created_again = httpx.post(
        server.base + "/api/conversation/conversations",
        json={
            "conversation_id": _CONVERSATION_ID,
            "backend_key": "codex",
            # Every conversation is started on a named model. A name is enough here:
            # what this file is about is whether the runtime is carried between tests,
            # and nothing in it ever spawns a backend.
            "model": "e2e-model",
        },
        timeout=10.0,
    )
    assert created_again.status_code == 201, created_again.text
