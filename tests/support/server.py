"""Shared process handles and public HTTP helpers, without browser dependencies."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from planner.conversation.events import ConversationEventPayload
from planner.conversation.storage import ConversationStore

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = Path(sys.executable).parent / "panels"
FAKE_NOW = "2026-07-04T12:00:00"
WAIT_MS = 10_000  # every Playwright wait
BOOT_BUDGET_S = 15.0  # server readiness budget

# A decoded JSON object, as the API and the CLI's --json hand one back. The values stay
# ``Any`` on purpose: this is somebody else's JSON, and pretending to know its shape here
# would be a fiction the tests would then have to fight.
type JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ServerHandle:
    base: str
    proc: subprocess.Popen[bytes]
    db_path: Path
    log_path: Path
    port: int
    control_socket_path: Path


class ApiHelper:
    """The three ways a test reaches the server's JSON API.

    A named class rather than a ``SimpleNamespace``: attribute access on a namespace is
    ``Any``, so every call a test made through this fixture was invisible to the type
    checker. The call syntax in the tests is unchanged.
    """

    def get(self, server: ServerHandle, path: str) -> JsonObject:
        resp = httpx.get(server.base + path, timeout=10.0)
        assert resp.status_code < 300, f"GET {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_post(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # No X-Plan-* headers: authctx classifies this request as unattributed,
        # which the direct-only /accept and the direct-only PATCH fields permit.
        resp = httpx.post(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"POST {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_patch(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        # A headerless PATCH is unattributed. The day brief writer is direct-only,
        # so this is how a test seeds or edits a brief.
        resp = httpx.patch(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"PATCH {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer

    def direct_put(self, server: ServerHandle, path: str, json_body: JsonObject) -> JsonObject:
        resp = httpx.put(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"PUT {path} -> {resp.status_code}: {resp.text}"
        answer: JsonObject = resp.json()
        return answer


def _append_rows(
    server: ServerHandle, conversation_id: str, *payloads: ConversationEventPayload
) -> None:
    store = ConversationStore(str(server.db_path))

    async def write() -> None:
        for payload in payloads:
            await store.append_event(conversation_id, payload)

    failures: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(write())
        except BaseException as error:  # noqa: BLE001 - re-raised on the browser thread
            failures.append(error)

    writer = threading.Thread(target=run)
    writer.start()
    writer.join(timeout=10)
    assert not writer.is_alive()
    if failures:
        raise failures[0]
